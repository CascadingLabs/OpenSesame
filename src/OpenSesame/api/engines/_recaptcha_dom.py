"""Shared reCAPTCHA frame access — same-origin **and** cross-origin.

A reCAPTCHA challenge lives in two iframes (the ``anchor`` checkbox frame and the
``bframe`` challenge frame). On Google's own ``api2/demo`` those frames are
same-origin, so the old engines read them through ``iframe.contentDocument``. On a
**real third-party site** the frames are served from ``google.com`` — cross-origin
— and ``contentDocument`` is ``null`` to the parent under the same-origin policy.

VoidCrawl 0.3.5 added ``page.eval_js_in_frame(url_pattern, expr)``: it runs the
expression inside the target frame's *own* CDP execution context, where
``document`` is that frame's document and the origin check is satisfied. That
works for same-origin and cross-origin frames alike, so the engines drive the
challenge through :class:`FrameAccess` and never touch ``contentDocument``.

One launch prerequisite for cross-origin: Chrome field-trial-isolates a few
origins (notably ``google.com``) out-of-process regardless of the usual flags, so
the session must be launched with ``extra_args=["disable-site-isolation-trials"]``
to keep the reCAPTCHA frames in-process and reachable. When that flag is missing
the frame is unreachable and :meth:`FrameAccess.eval_frame` raises
:class:`FrameUnreachable` with ``isolated=True`` — turned into an actionable
error rather than a vague miss.
"""

from __future__ import annotations

from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.result import SolveResult, SolveStatus

# Frame URL substrings — reCAPTCHA v2 and Enterprise differ only in this path
# segment (``recaptcha/api2/`` vs ``recaptcha/enterprise/``). Try both; exactly
# one is present, so the match is unambiguous.
BFRAME_PATTERNS = ("api2/bframe", "enterprise/bframe")
ANCHOR_PATTERNS = ("api2/anchor", "enterprise/anchor")

# Parent-document selector for the challenge iframe *element* (its rect is
# readable cross-origin; only its contentDocument is not).
BFRAME_SELECTOR = 'iframe[src*="api2/bframe"], iframe[src*="enterprise/bframe"]'

# The minted token lives in the **parent** document's response field, not in the
# frame — always read it from the top context.
TOKEN_JS = (
    "document.querySelector('#g-recaptcha-response, "
    "textarea[name=\"g-recaptcha-response\"]')?.value || ''"
)

# Parent: the challenge iframe element's on-page rect (for screenshot offset).
IFRAME_RECT_JS = f"""
(() => {{
  const f = document.querySelector('{BFRAME_SELECTOR}');
  if (!f) return null;
  const r = f.getBoundingClientRect();
  return {{left:r.left, top:r.top, width:r.width, height:r.height}};
}})()
"""


class FrameUnreachable(Exception):
    """A reCAPTCHA challenge frame could not be driven — with a typed reason.

    The three causes are genuinely different fixes, so they are distinguished by
    a stable ``reason`` code (also surfaced on the ``SolveResult.metadata``):

    - ``frame_isolated`` — the frame is tracked but out-of-process; the
      browser/session owner must launch with ``disable-site-isolation-trials``.
    - ``voidcrawl_too_old`` — the page has no ``eval_js_in_frame`` (VoidCrawl
      < 0.3.5); upgrade VoidCrawl.
    - ``frame_absent`` — there is no reCAPTCHA frame on the page at all.
    """

    ISOLATED = "frame_isolated"
    UNSUPPORTED = "voidcrawl_too_old"
    ABSENT = "frame_absent"

    _MESSAGES = {
        ISOLATED: "reCAPTCHA frame is cross-origin and out-of-process (needs "
                  "disable-site-isolation-trials at browser launch)",
        UNSUPPORTED: "VoidCrawl build has no eval_js_in_frame (needs voidcrawl>=0.3.5)",
        ABSENT: "no reCAPTCHA challenge frame on the page",
    }

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(self._MESSAGES.get(reason, reason))

    @property
    def isolated(self) -> bool:
        return self.reason == self.ISOLATED


class FrameAccess:
    """Drives the reCAPTCHA frames via CDP frame-scoped eval (origin-agnostic)."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self._eval_in_frame = (
            getattr(page, "eval_js_in_frame", None)
            or getattr(page, "evaluate_js_in_frame", None)
        )

    async def eval_frame(self, patterns: tuple[str, ...], js: str) -> Any:
        """Run ``js`` inside the first frame matching ``patterns``.

        Raises :class:`FrameUnreachable` if no matching frame has a reachable
        execution context (absent, or out-of-process without the isolation flag).
        """

        if self._eval_in_frame is None:  # voidcrawl < 0.3.5
            raise FrameUnreachable(FrameUnreachable.UNSUPPORTED)
        for pattern in patterns:
            try:
                return await self._eval_in_frame(pattern, js)
            except Exception:  # FrameNotFound / AmbiguousFrame — try the next pattern
                continue
        # No pattern resolved: tracked-but-unreachable (isolated) vs. truly absent.
        listed = await self._frame_listed(patterns)
        raise FrameUnreachable(FrameUnreachable.ISOLATED if listed else FrameUnreachable.ABSENT)

    async def parent_eval(self, js: str) -> Any:
        return await self.page.eval_js(js)

    async def token(self) -> str:
        return str(await self.page.eval_js(TOKEN_JS) or "")

    async def iframe_rect(self) -> dict | None:
        rect = await self.page.eval_js(IFRAME_RECT_JS)
        return rect if isinstance(rect, dict) else None

    async def _frame_listed(self, patterns: tuple[str, ...]) -> bool:
        """True if the browser tracks a frame whose URL matches — i.e. it exists
        but its context is unreachable (out-of-process)."""

        frame_urls = getattr(self.page, "frame_urls", None)
        if not callable(frame_urls):
            return False
        try:
            urls = await frame_urls()
        except Exception:
            return False
        return any(any(p in str(u) for p in patterns) for u in urls)


# Per-reason caller-facing message + the action that fixes it. The browser/session
# owner — not OpenSesame, which never launches the browser — owns the launch flag
# (in the Cascading stack that is Yosoi's fetcher).
_UNREACHABLE_ERRORS = {
    FrameUnreachable.ISOLATED: (
        "cannot drive the reCAPTCHA challenge frame: it is cross-origin and isolated "
        "out-of-process. The browser/session owner (e.g. Yosoi's fetcher) must launch "
        'with extra_args=["disable-site-isolation-trials"].'
    ),
    FrameUnreachable.UNSUPPORTED: (
        "cannot drive the reCAPTCHA challenge frame: this VoidCrawl build has no "
        "eval_js_in_frame. Upgrade to voidcrawl>=0.3.5."
    ),
    FrameUnreachable.ABSENT: "no reCAPTCHA challenge frame on the page",
}
_UNREACHABLE_REMEDIATION = {
    FrameUnreachable.ISOLATED: 'launch the browser with extra_args=["disable-site-isolation-trials"]',
    FrameUnreachable.UNSUPPORTED: "upgrade to voidcrawl>=0.3.5",
}


def frame_unreachable_result(challenge: Challenge, exc: FrameUnreachable, *, strategy: str) -> SolveResult:
    """Map a :class:`FrameUnreachable` to an honest, actionable FAILED result.

    ``metadata.reason`` carries the stable code (``frame_isolated`` /
    ``voidcrawl_too_old`` / ``frame_absent``) so a caller can branch on it; the
    isolated case also keeps ``frame_isolated=True`` and a ``remediation`` string.
    """

    metadata: dict[str, Any] = {"strategy": strategy, "reason": exc.reason}
    if exc.reason == FrameUnreachable.ISOLATED:
        metadata["frame_isolated"] = True
    if exc.reason in _UNREACHABLE_REMEDIATION:
        metadata["remediation"] = _UNREACHABLE_REMEDIATION[exc.reason]
    return SolveResult(
        status=SolveStatus.FAILED, family=challenge.family,
        error=_UNREACHABLE_ERRORS.get(exc.reason, str(exc)),
        metadata=metadata,
    )
