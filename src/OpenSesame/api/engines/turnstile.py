"""Cloudflare Turnstile — locate the checkbox via AX, humanized compositor click.

Turnstile is unlike reCAPTCHA: there is **no image/audio puzzle to classify**. It
passes on browser *legitimacy* (stealth fingerprint, JS proof-of-work, behaviour)
plus — for the managed/interactive widget — a single click on the "Verify you are
human" checkbox. That checkbox is a real ``<input type="checkbox">`` but it lives
in a **closed shadow root** inside a **cross-origin** ``challenges.cloudflare.com``
iframe, so page JS (`contentDocument`/`shadowRoot` are null) cannot reach it.

VoidCrawl 0.3.6's frame-rooted accessibility locator (`ax_box_in_frame`) reads the
browser-computed AX tree, which descends into closed shadow roots and the
cross-origin frame, and returns the checkbox's on-page rect — with **no shadow-DOM
tampering** (which trips Turnstile's closed-shadow check, ERROR 600010). We then
drive a **humanized** compositor click on that rect (a trusted event — a DOM
`.click()` is untrusted and rejected) and harvest the minted ``cf-turnstile-response``
token from the parent form.

Note: Cloudflare *test* sitekeys (``1x…`` / ``2x…`` / ``3x…``) mint a dummy token
(``XXXX.DUMMY.TOKEN.XXXX``) that only validates against test secret keys — useful
to prove the mechanics; real success additionally depends on IP/browser reputation.
"""

from __future__ import annotations

import asyncio
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    TokenSolution,
)

# The Turnstile widget/challenge iframe (v2 + managed challenge share this host).
CF_FRAME = "challenges.cloudflare.com"
CHECKBOX_ROLE = "checkbox"
CHECKBOX_NAME = "Verify you are human"   # accessible name on the en-US widget

# The minted token lands in the parent form's hidden response field.
_TOKEN_JS = (
    "(() => { const e = document.querySelector('input[name=\"cf-turnstile-response\"]');"
    " return e ? (e.value || '') : ''; })()"
)


class TurnstileEngine:
    """Solves Cloudflare Turnstile by clicking its checkbox (AX-located)."""

    family = Family.TURNSTILE

    def __init__(self, *, max_attempts: int = 2) -> None:
        self.max_attempts = max_attempts

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        return []  # no local model — Turnstile is behavioural, not a puzzle

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        token = await self._token(page)
        if token:                                   # already passed (invisible/managed)
            return self._ok(challenge, token)

        if not await self._has_frame(page):
            return self._fail(challenge, "no Cloudflare Turnstile frame on page")

        # Two modes with different success criteria:
        #  - embedded widget  → mint a cf-turnstile-response token
        #  - full-page "managed challenge" interstitial → the wall *clears* (no token)
        interstitial = await self._is_interstitial(page)

        for _ in range(self.max_attempts):
            clicked = await self._click_checkbox(page)
            if not clicked:
                outcome = await self._wait_outcome(challenge, page, interstitial, tries=6)
                if outcome is not None:             # auto-passed / cleared
                    return outcome
                if not await self._frame_reachable(page):
                    return self._fail(
                        challenge,
                        "Turnstile frame is out-of-process — launch the session with "
                        'extra_args=["disable-site-isolation-trials"]',
                        frame_isolated=True,
                    )
                return self._fail(challenge, "Turnstile checkbox not found in challenge frame")

            await asyncio.sleep(0.4)                 # let the click settle / token populate
            outcome = await self._wait_outcome(challenge, page, interstitial)
            if outcome is not None:
                return outcome

        if interstitial:
            # No token to mint here, and the interstitial never cleared: this is the
            # reputation-gated managed challenge, not a mechanics failure.
            return self._fail(
                challenge,
                "Cloudflare full-page managed challenge did not clear — it is "
                "reputation-gated (clearance, not a widget token); rotate proxy/profile",
                reason="managed_challenge",
            )
        return self._fail(challenge, "no cf-turnstile-response token after clicking")

    # -- internals --------------------------------------------------------

    async def _click_checkbox(self, page) -> bool:
        """AX-locate the checkbox in the cross-origin closed-shadow frame and
        click it with VoidCrawl's **humanized** compositor pointer (CAS-147)."""

        click = getattr(page, "click_ax_in_frame", None)
        if not callable(click):  # voidcrawl < 0.3.6
            return False
        for name in (CHECKBOX_NAME, ""):   # exact name first, then any checkbox
            try:
                await click(CF_FRAME, CHECKBOX_ROLE, name, humanize=True)
                return True
            except Exception:
                continue
        return False

    async def _wait_outcome(self, challenge, page, interstitial: bool, *, tries: int = 12):
        """Poll for either a minted token (widget) or a cleared wall (interstitial)."""

        for _ in range(tries):
            token = await self._token(page)
            if token:
                return self._ok(challenge, token)
            if interstitial and await self._cleared(page):
                return self._cleared_ok(challenge)
            await asyncio.sleep(0.5)
        return None

    async def _is_interstitial(self, page) -> bool:
        """True for the full-page 'Just a moment…' managed challenge (vs an
        embedded widget on a normal page)."""

        try:
            return bool(await page.eval_js(
                "(() => /just a moment|checking your browser|performing security|"
                "needs to review the security/i.test("
                "(document.title||'') + ' ' + "
                "(document.body ? document.body.innerText.slice(0,300) : '')))()"
            ))
        except Exception:
            return False

    async def _cleared(self, page) -> bool:
        """The wall is gone — Cloudflare granted clearance and left the interstitial."""

        detect = getattr(page, "detect_captcha", None)
        if callable(detect):
            try:
                return not await detect()
            except Exception:
                pass
        # Fallback: the challenge frame disappeared.
        return not await self._frame_reachable(page)

    async def _has_frame(self, page) -> bool:
        return await self._frame_reachable(page)

    async def _frame_reachable(self, page) -> bool:
        frame_urls = getattr(page, "frame_urls", None)
        if not callable(frame_urls):
            return False
        try:
            urls = await frame_urls()
        except Exception:
            return False
        return any(CF_FRAME in str(u) for u in urls)

    async def _token(self, page) -> str:
        return str(await page.eval_js(_TOKEN_JS) or "")

    async def _wait_token(self, page, *, tries: int = 12) -> str:
        for _ in range(tries):
            token = await self._token(page)
            if token:
                return token
            await asyncio.sleep(0.5)
        return ""

    def _ok(self, challenge, token) -> SolveResult:
        return SolveResult(
            status=SolveStatus.SOLVED, family=challenge.family,
            solution=TokenSolution(token), solved_by=SolvedBy.LOCAL,
            vendor=challenge.vendor_kind or "cloudflare",
            metadata={"strategy": "turnstile-checkbox"},
        )

    def _cleared_ok(self, challenge) -> SolveResult:
        """The full-page managed challenge cleared — wall passed in-session, no token."""

        return SolveResult(
            status=SolveStatus.SOLVED, family=challenge.family,
            solution=None, solved_by=SolvedBy.LOCAL,
            vendor=challenge.vendor_kind or "cloudflare",
            metadata={"strategy": "turnstile-challenge", "cleared": True},
        )

    def _fail(self, challenge, error: str, *, frame_isolated: bool = False,
              reason: str | None = None) -> SolveResult:
        metadata: dict[str, Any] = {"strategy": "turnstile-checkbox"}
        if frame_isolated:
            metadata["frame_isolated"] = True
        if reason:
            metadata["reason"] = reason
        return SolveResult(status=SolveStatus.FAILED, family=challenge.family, error=error,
                           metadata=metadata)
