"""Cloudflare Turnstile — two genuinely different variants behind one widget.

**1. Embedded widget** (a `.cf-turnstile` div on a form): pass = a single click on
the "Verify you are human" checkbox, which mints a ``cf-turnstile-response`` token.
The checkbox is a real ``<input type="checkbox">`` in a **closed shadow root** inside
a **cross-origin** ``challenges.cloudflare.com`` iframe — page JS can't reach it
(`contentDocument`/`shadowRoot` are null). VoidCrawl 0.3.6's frame-rooted AX locator
(`ax_box_in_frame`) reads the browser-computed accessibility tree, which descends into
closed shadow roots, and returns the checkbox rect with **no shadow tampering** (that
trips Turnstile's closed-shadow check, ERROR 600010). We drive a **humanized**
compositor click (trusted; a DOM `.click()` is rejected) and harvest the token.

**2. Full-page Managed Challenge** ("Just a moment…" interstitial): this is **not a
token and not a click** — it is an edge-enforced browser-trust gate decided by
**CDP/automation detection**. A browser that enables almost no CDP domains
auto-clears it in a few seconds with no interaction; a loud CDP session never does.
So OpenSesame does **not** click here — it detects the interstitial and **awaits the
clearance** that the *minimal-stealth browser* produces. Launch VoidCrawl with
``VOIDCRAWL_STEALTH_NO_RUNTIME`` (it skips Runtime/Network/Performance/Log/autoAttach/
isolated-world enables) so the wall clears. (CAS-217.)

Note: the 2captcha widget demo uses a Cloudflare *test* sitekey (``3x…FF``) → a dummy
``XXXX.DUMMY.TOKEN.XXXX`` token (mechanics proof; real tokens also need reputation).
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
    """Cloudflare Turnstile: widget → token (AX click); managed challenge → await clearance."""

    family = Family.TURNSTILE

    def __init__(self, *, max_attempts: int = 2, clearance_tries: int = 20) -> None:
        self.max_attempts = max_attempts
        self.clearance_tries = clearance_tries   # managed-challenge clearance poll (~1s each)

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
        # Variant 2 — full-page Managed Challenge interstitial. Not a token, not a
        # click: a minimal-CDP-footprint browser auto-clears it. Detect + await.
        if await self._is_interstitial(page):
            return await self._await_clearance(challenge, page)

        # Variant 1 — embedded widget: mint cf-turnstile-response via the checkbox.
        token = await self._token(page)
        if token:                                   # already minted (invisible/auto)
            return self._ok(challenge, token)
        if not await self._frame_reachable(page):
            return self._fail(challenge, "no Cloudflare Turnstile frame on page")

        for _ in range(self.max_attempts):
            if not await self._click_checkbox(page):
                token = await self._wait_token(page, tries=6)   # may have auto-passed
                if token:
                    return self._ok(challenge, token)
                if not await self._frame_reachable(page):
                    return self._fail(
                        challenge,
                        "Turnstile frame is out-of-process — launch the session with "
                        'extra_args=["disable-site-isolation-trials"]',
                        frame_isolated=True,
                    )
                return self._fail(challenge, "Turnstile checkbox not found in challenge frame")

            await asyncio.sleep(0.4)                 # let the click settle / token populate
            token = await self._wait_token(page)
            if token:
                return self._ok(challenge, token)

        return self._fail(challenge, "no cf-turnstile-response token after clicking")

    # -- internals --------------------------------------------------------

    async def _await_clearance(self, challenge, page) -> SolveResult:
        """Wait for the Managed Challenge to clear — the *minimal-stealth browser*
        does the work; we do not click. Honest failure names the requirement."""

        for _ in range(self.clearance_tries):
            if await self._cleared(page):
                return self._cleared_ok(challenge)
            await asyncio.sleep(1.0)
        return self._fail(
            challenge,
            "Cloudflare managed challenge did not clear — it is decided by CDP/automation "
            "detection, not a click. Launch the browser in minimal-stealth mode "
            "(VOIDCRAWL_STEALTH_NO_RUNTIME, which enables almost no CDP domain) so it clears.",
            reason="managed_challenge",
        )

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
        """The interstitial is gone — Cloudflare granted clearance and navigated to
        the real page. Detected by the title (the destination page may have its OWN
        Turnstile widget, so ``detect_captcha`` would still say 'turnstile')."""

        try:
            title = str(await page.eval_js("document.title") or "")
        except Exception:
            return False
        t = title.lower()
        return bool(title) and "just a moment" not in t and "checking your browser" not in t

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
