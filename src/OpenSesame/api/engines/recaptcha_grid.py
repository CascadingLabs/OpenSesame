"""reCAPTCHA v2 image grid — local tile classifier, driven via frame-scoped eval.

Reads the challenge frame (``bframe``) through ``page.eval_js_in_frame`` so it
works whether that frame is same-origin (``api2/demo``) or cross-origin (a real
third-party site). The inner JS runs in the frame's own context, so ``document``
is the challenge document directly — no ``contentDocument``. Tile rects come back
frame-relative; the parent supplies the iframe element's on-page offset, so the
grid screenshot is cropped in page coordinates. Tiles are clicked **via the
frame's DOM** (``td.click()``), not pixel coordinates. The minted token is read
from the parent document. Dynamic ("click verify once there are none left")
challenges loop until a round selects nothing new.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines._recaptcha_dom import (
    ANCHOR_PATTERNS,
    BFRAME_PATTERNS,
    FrameAccess,
    FrameUnreachable,
    frame_unreachable_result,
)
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    TokenSolution,
)

DEFAULT_MODEL = "verytuffcat/recaptcha"
PROVIDER_KIND = "tiles"

# Structured grid state — runs INSIDE the bframe (document is the challenge doc).
# Tile rects are frame-relative; the parent adds the iframe's page offset.
_GRID_STATE_FRAME = r"""
(() => {
  const instr = document.querySelector('.rc-imageselect-instructions');
  const table = document.querySelector('#rc-imageselect-target table');
  if (!table) return {present:false};
  const cls = table.className || '';
  const m = cls.match(/rc-imageselect-table-(\d)(\d)/);
  const rows = m ? parseInt(m[1],10) : 3, cols = m ? parseInt(m[2],10) : 3;
  const tds = Array.from(table.querySelectorAll('td'));
  let left=1e9, top=1e9, right=0, bottom=0;
  const cells = tds.map((td, i) => {
    const r = td.getBoundingClientRect();
    left=Math.min(left,r.left); top=Math.min(top,r.top);
    right=Math.max(right,r.right); bottom=Math.max(bottom,r.bottom);
    const sel = td.getAttribute('aria-pressed')==='true'
      || td.className.indexOf('selected')!==-1;   // tileselected (one-shot) or dynamic-selected
    return {index:i, row:Math.floor(i/cols), col:i%cols, selected:!!sel};
  });
  return {
    present:true,
    instructions: instr ? instr.innerText.replace(/\s+/g,' ').trim() : '',
    rows, cols, cells,
    frame_grid: {left, top, right, bottom},
  };
})()
"""

# Click the td at a given flat index — runs inside the bframe.
_CLICK_CELL_FRAME = r"""
(() => {
  const tds = document.querySelectorAll('#rc-imageselect-target table td');
  if (!tds || !tds[__I__]) return false;
  tds[__I__].click(); return true;
})()
"""

_CLICK_VERIFY_FRAME = r"""
(() => {
  const el = document.querySelector('#recaptcha-verify-button');
  if (!el) return false;
  el.click(); return true;
})()
"""

# Is the image challenge already open? — runs inside the bframe.
_CHALLENGE_OPEN_FRAME = "(() => !!document.querySelector('#rc-imageselect'))()"
_TABLE_READY_FRAME = "(() => !!document.querySelector('#rc-imageselect-target table'))()"
# Click the checkbox — runs inside the anchor frame.
_CLICK_ANCHOR_FRAME = (
    "(() => { const cb = document.querySelector('#recaptcha-anchor');"
    " if (cb) { cb.click(); return true; } return false; })()"
)


class RecaptchaGridEngine:
    """Solves a reCAPTCHA v2 image-grid challenge via frame-scoped DOM clicks."""

    family = Family.RECAPTCHA_V2

    def __init__(self, *, default_model: str = DEFAULT_MODEL, max_rounds: int = 6) -> None:
        self.default_model = default_model
        self.max_rounds = max_rounds

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        model_id = policy.models.get("recaptcha_v2_grid") or self.default_model
        return [ModelKey(kind=PROVIDER_KIND, model_id=model_id, device=policy.device)]

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        key = self.model_keys(policy)[0]
        selector = registry.get(key)  # load-once, cached for the process
        return await self._solve(challenge, FrameAccess(page), page, selector, key.model_id, policy.device)

    async def _open_challenge(self, frames: FrameAccess, *, tries: int = 12) -> None:
        """Click the anchor checkbox to open the image challenge, if needed."""

        try:
            if await frames.eval_frame(BFRAME_PATTERNS, _CHALLENGE_OPEN_FRAME):
                return
        except FrameUnreachable:
            pass  # bframe may not exist until the checkbox is clicked
        try:
            await frames.eval_frame(ANCHOR_PATTERNS, _CLICK_ANCHOR_FRAME)
        except FrameUnreachable:
            return  # no anchor (invisible variant / already present) — let _solve probe
        for _ in range(tries):
            try:
                if await frames.eval_frame(BFRAME_PATTERNS, _TABLE_READY_FRAME):
                    return
            except FrameUnreachable:
                pass
            await asyncio.sleep(0.5)

    async def _solve(self, challenge, frames, page, selector, model_id, device) -> SolveResult:
        await self._open_challenge(frames)
        with tempfile.TemporaryDirectory() as tmp:
            for round_index in range(self.max_rounds):
                token = await frames.token()
                if token:
                    return self._ok(challenge, token, model_id, device)
                try:
                    state = await frames.eval_frame(BFRAME_PATTERNS, _GRID_STATE_FRAME)
                except FrameUnreachable as exc:
                    return frame_unreachable_result(challenge, exc, strategy="grid")
                if not isinstance(state, dict):
                    return self._fail(challenge, "grid state unreadable")
                if not state.get("present"):
                    break

                target = parse_target(state.get("instructions", ""))
                if not target:
                    return self._fail(challenge, f"no target parsed: {state.get('instructions')!r}")
                rows, cols = int(state["rows"]), int(state["cols"])
                grid = self._page_bbox(state["frame_grid"], await frames.iframe_rect())
                if grid is None:
                    return self._fail(challenge, "could not locate the grid on the page")
                image = Path(tmp) / f"grid-{round_index}.png"
                await page.screenshot(path=str(image), bbox=grid)

                picks = await asyncio.to_thread(
                    selector.select_tiles, str(image), rows=rows, cols=cols, target=target,
                )
                selected = {(c["row"], c["col"]) for c in state["cells"] if c.get("selected")}
                clicked = 0
                for row, col, _score in picks:
                    if (row, col) in selected:
                        continue
                    js = _CLICK_CELL_FRAME.replace("__I__", str(row * cols + col))
                    if await frames.eval_frame(BFRAME_PATTERNS, js):
                        clicked += 1
                if clicked == 0:
                    break
                await asyncio.sleep(1.0)  # let dynamic tiles settle before verify/next round

            try:
                await frames.eval_frame(BFRAME_PATTERNS, _CLICK_VERIFY_FRAME)
            except FrameUnreachable as exc:
                return frame_unreachable_result(challenge, exc, strategy="grid")
            token = await self._read_token_after(frames)
            if token:
                return self._ok(challenge, token, model_id, device)
        return self._fail(challenge, "no token after grid rounds")

    @staticmethod
    def _page_bbox(frame_grid: Any, iframe_rect: Any) -> tuple[int, int, int, int] | None:
        """Page-absolute crop = iframe's page offset + frame-relative grid rect."""

        if not isinstance(frame_grid, dict) or not isinstance(iframe_rect, dict):
            return None
        x = iframe_rect["left"] + frame_grid["left"]
        y = iframe_rect["top"] + frame_grid["top"]
        w = frame_grid["right"] - frame_grid["left"]
        h = frame_grid["bottom"] - frame_grid["top"]
        return (int(x), int(y), int(w), int(h))

    async def _read_token_after(self, frames: FrameAccess, *, tries: int = 12) -> str:
        for _ in range(tries):
            token = await frames.token()
            if token:
                return token
            await asyncio.sleep(0.5)
        return ""

    def _ok(self, challenge, token, model_id, device) -> SolveResult:
        return SolveResult(
            status=SolveStatus.SOLVED, family=challenge.family,
            solution=TokenSolution(token), solved_by=SolvedBy.LOCAL,
            vendor=challenge.vendor_kind, model_id=model_id, device=device,
            metadata={"strategy": "grid"},
        )

    def _fail(self, challenge, error: str) -> SolveResult:
        return SolveResult(status=SolveStatus.FAILED, family=challenge.family, error=error,
                           metadata={"strategy": "grid"})


def parse_target(instructions: str) -> str | None:
    text = re.sub(r"\s+", " ", (instructions or "").strip().lower())
    for pat in (
        r"select all (?:images|squares) with (?:a )?([a-z ]+?)(?: if| click|$)",
        r"select all ([a-z ]+?)(?: if| click|$)",
    ):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return None
