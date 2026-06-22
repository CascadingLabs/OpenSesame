"""hCaptcha frame access — checkbox + canvas-challenge frames over CDP frame-eval.

hCaptcha renders two same-asset iframes from ``newassets.hcaptcha.com/.../hcaptcha.html``,
distinguished only by their URL *fragment*: ``#frame=checkbox`` (the anchor checkbox)
and ``#frame=challenge`` (the popup that paints the image challenge to a ``<canvas>``).
Both are reached via ``page.eval_js_in_frame(pattern, js)`` — the fragment is part of the
frame URL CDP matches on (verified live), so the two patterns below disambiguate them.

The minted token lands in the **parent** document's ``h-captcha-response`` textarea
(hCaptcha also fills ``g-recaptcha-response`` under recaptchacompat); always read it from
the top context. The challenge cells are painted pixels with a zero-size accessibility
mirror, so they are clicked on the canvas at page coordinates — this module supplies the
geometry (canvas page bbox) and the DOM-driveable bits (open / state / submit / reload);
the engine owns the screenshot, the model call, and the humanized click.
"""

from __future__ import annotations

from typing import Any

# Frame URL substrings — the hash fragment is part of the matched frame URL.
CHECKBOX_FRAME = "frame=checkbox"
CHALLENGE_FRAME = "frame=challenge"

# Parent: the minted token (read from the top document, never the frame).
TOKEN_JS = "document.querySelector('textarea[name=\"h-captcha-response\"]')?.value || ''"

# Parent: the challenge iframe element's on-page rect (for the screenshot offset).
_CHALLENGE_IFRAME_SELECTOR = 'iframe[src*="frame=challenge"]'
IFRAME_RECT_JS = f"""
(() => {{
  const f = document.querySelector('{_CHALLENGE_IFRAME_SELECTOR}');
  if (!f) return null;
  const r = f.getBoundingClientRect();
  return {{left:r.left, top:r.top, width:r.width, height:r.height}};
}})()
"""

# Checkbox frame: click the anchor checkbox to open the challenge.
CLICK_CHECKBOX_JS = (
    "(() => { const cb = document.getElementById('checkbox')"
    " || document.querySelector('[role=\"checkbox\"]');"
    " if (cb) { cb.click(); return true; } return false; })()"
)

# Challenge frame: is the canvas painted yet?
CANVAS_READY_JS = "(() => !!document.querySelector('canvas'))()"

# Challenge frame: prompt text + canvas frame-relative rect + submit label + breadcrumbs.
STATE_JS = r"""
(() => {
  const norm = s => (s || '').replace(/\s+/g,' ').trim();
  const promptEl = document.querySelector('.prompt-text')
    || document.querySelector('.challenge-prompt');
  const cv = document.querySelector('canvas');
  let canvas = null;
  if (cv) {
    const r = cv.getBoundingClientRect();
    canvas = {left:r.left, top:r.top, width:r.width, height:r.height};
  }
  const sb = document.querySelector('.button-submit');
  return {
    present: !!cv,
    prompt: norm(promptEl ? promptEl.textContent : ''),
    canvas,
    submit: sb ? norm(sb.textContent) : null,
    crumbs: document.querySelectorAll('.Crumb').length,
  };
})()
"""

# Challenge frame: click submit / next.
CLICK_SUBMIT_JS = (
    "(() => { const b = document.querySelector('.button-submit');"
    " if (b) { b.click(); return true; } return false; })()"
)

# Challenge frame: click the reload / new-challenge control (best-effort across skins).
CLICK_RELOAD_JS = (
    "(() => { const b = document.querySelector("
    "'.refresh.button, .button-refresh, .reload,"
    " [aria-label*=\"new challenge\" i], [aria-label*=\"reload\" i]');"
    " if (b) { b.click(); return true; } return false; })()"
)


def page_bbox(iframe_rect: Any, canvas_rect: Any) -> tuple[int, int, int, int] | None:
    """Page-absolute canvas crop = challenge-iframe page offset + canvas frame rect.

    All values are CSS pixels (``getBoundingClientRect``), so the engine maps the
    model's normalized point onto this bbox proportionally — devicePixelRatio drops
    out and the high-DPR canvas backing store never enters the click math.
    """

    if not isinstance(iframe_rect, dict) or not isinstance(canvas_rect, dict):
        return None
    x = iframe_rect["left"] + canvas_rect["left"]
    y = iframe_rect["top"] + canvas_rect["top"]
    return (int(x), int(y), int(canvas_rect["width"]), int(canvas_rect["height"]))


class HcaptchaFrames:
    """Thin async wrapper over the page's frame-scoped + parent JS eval."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self._eval_in_frame = (
            getattr(page, "eval_js_in_frame", None)
            or getattr(page, "evaluate_js_in_frame", None)
        )

    async def in_frame(self, pattern: str, js: str) -> Any:
        if self._eval_in_frame is None:  # voidcrawl < 0.3.5
            raise RuntimeError("page has no eval_js_in_frame (needs voidcrawl>=0.3.5)")
        return await self._eval_in_frame(pattern, js)

    async def parent(self, js: str) -> Any:
        return await self.page.eval_js(js)

    async def token(self) -> str:
        return str(await self.page.eval_js(TOKEN_JS) or "")

    async def iframe_rect(self) -> dict | None:
        rect = await self.page.eval_js(IFRAME_RECT_JS)
        return rect if isinstance(rect, dict) else None
