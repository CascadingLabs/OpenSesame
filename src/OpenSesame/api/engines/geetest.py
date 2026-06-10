"""GeeTest v3 slider — same-origin canvas gap detection + humanized drag.

The 2Captcha GeeTest demo runs GeeTest v3 "fullpage" in the *main document* (no
iframe): three same-origin ``<canvas>`` elements — ``geetest_canvas_fullbg``
(complete image), ``geetest_canvas_bg`` (image with the puzzle notch), and
``geetest_canvas_slice`` (the draggable piece). Because they are same-origin we
read their pixels with ``getImageData`` and locate the gap by a column-wise diff
of fullbg vs bg — no model. The piece is then dragged with a human-like
accelerate / overshoot / settle curve via CDP mouse events.

On success GeeTest mints the real triple (``geetest_challenge`` /
``geetest_validate`` / ``geetest_seccode``). The geometry is fully solved here;
GeeTest v3 additionally scores the *session* (its ``gct`` behavioural tracker and
the encrypted ``w`` validation payload). On an automated headless session that
behavioural layer rejects the solve (no ``ajax.php`` validation fires) — that is
the anti-bot track (CAS-180/182's behavioural moat), not the slider solver. The
engine reports it honestly with ``route="anti-bot"``.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    Timing,
    TokenSolution,
)

GEETEST_STATE_JS = r"""
(() => {
  const radar = document.querySelector('.geetest_radar_btn');
  const validate = document.querySelector('input[name="geetest_validate"]');
  const rr = radar ? radar.getBoundingClientRect() : null;
  return {
    radar: rr ? {x: rr.left, y: rr.top, width: rr.width, height: rr.height} : null,
    validate: validate ? (validate.value || '') : '',
  };
})()
"""

GEETEST_DETECT_JS = r"""
(() => {
  const bg = document.querySelector('.geetest_canvas_bg');
  const full = document.querySelector('.geetest_canvas_fullbg');
  const slice = document.querySelector('.geetest_canvas_slice');
  const sliderBtn = document.querySelector('.geetest_slider_button');
  if (!bg || !full) return {ok: false, reason: 'no-canvas'};
  const W = bg.width, H = bg.height;
  if (!W || !H) return {ok: false, reason: 'empty-canvas'};
  let bgd, fud;
  try {
    bgd = bg.getContext('2d').getImageData(0, 0, W, H).data;
    fud = full.getContext('2d').getImageData(0, 0, W, H).data;
  } catch (e) { return {ok: false, reason: 'read:' + (e && e.message)}; }
  const colDiff = new Array(W).fill(0);
  for (let x = 0; x < W; x++) {
    let s = 0;
    for (let y = 0; y < H; y++) {
      const i = (y * W + x) * 4;
      s += Math.abs(bgd[i] - fud[i]) + Math.abs(bgd[i+1] - fud[i+1]) + Math.abs(bgd[i+2] - fud[i+2]);
    }
    colDiff[x] = s;
  }
  let maxd = 0;
  for (let x = 0; x < W; x++) if (colDiff[x] > maxd) maxd = colDiff[x];
  if (maxd <= 0) return {ok: false, reason: 'no-diff'};
  const thr = maxd * 0.35;
  let gapLeft = -1, gapRight = -1, miss = 0;
  for (let x = 4; x < W; x++) {
    if (colDiff[x] > thr) { if (gapLeft < 0) gapLeft = x; gapRight = x; miss = 0; }
    else if (gapLeft >= 0) { if (++miss > 8) break; }
  }
  let sliceLeft = 0;
  if (slice) {
    try {
      const W2 = slice.width, H2 = slice.height;
      const sd = slice.getContext('2d').getImageData(0, 0, W2, H2).data;
      outer: for (let x = 0; x < W2; x++)
        for (let y = 0; y < H2; y++)
          if (sd[(y * W2 + x) * 4 + 3] > 16) { sliceLeft = x; break outer; }
    } catch (e) {}
  }
  const dispW = bg.getBoundingClientRect().width || W;
  let btn = null;
  if (sliderBtn) { const r = sliderBtn.getBoundingClientRect();
    btn = {x: r.left, y: r.top, width: r.width, height: r.height}; }
  return {ok: true, gapLeft, sliceLeft, scale: dispW / W, slider_button: btn};
})()
"""


@dataclass(frozen=True)
class GeetestGap:
    ok: bool
    gap_left: int = -1
    slice_left: int = 0
    scale: float = 1.0
    button: dict[str, float] | None = None
    reason: str = ""

    @property
    def drag_css(self) -> float:
        return max(0.0, (self.gap_left - self.slice_left) * self.scale)


def parse_gap(raw: Any) -> GeetestGap:
    if not isinstance(raw, dict) or not raw.get("ok"):
        reason = raw.get("reason", "unreadable") if isinstance(raw, dict) else "unreadable"
        return GeetestGap(ok=False, reason=str(reason))
    return GeetestGap(
        ok=True,
        gap_left=int(raw.get("gapLeft", -1)),
        slice_left=int(raw.get("sliceLeft", 0)),
        scale=float(raw.get("scale", 1.0)),
        button=raw.get("slider_button") if isinstance(raw.get("slider_button"), dict) else None,
    )


def human_drag_path(start_x: float, start_y: float, distance: float, *,
                    steps: int = 34, seed: int = 19) -> list[tuple[float, float]]:
    """Accelerate, overshoot a few px, settle back — GeeTest scores the curve."""

    rng = random.Random(seed)
    overshoot = max(2.0, distance * 0.06) + rng.uniform(1.0, 4.0)
    peak = distance + overshoot
    pts: list[tuple[float, float]] = []
    for i in range(steps):
        t = i / (steps - 1)
        ease = t * t * t * (t * (t * 6 - 15) + 10)
        y = start_y + (rng.uniform(-1.3, 1.3) if 0 < i < steps - 1 else 0.0)
        pts.append((start_x + peak * ease, y))
    for i in range(1, 8):
        t = i / 7
        pts.append((start_x + peak + (distance - peak) * (t * t * (3 - 2 * t)),
                    start_y + rng.uniform(-0.8, 0.8)))
    return pts


class GeetestSlideEngine:
    """GeeTest v3 slider: canvas-diff gap detection + humanized drag → validate."""

    family = Family.GEETEST

    def __init__(self, *, settle_s: float = 8.0, result_wait_s: float = 6.0) -> None:
        self.settle_s = settle_s
        self.result_wait_s = result_wait_s

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        return []

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        started = time.time()
        state = await page.eval_js(GEETEST_STATE_JS)
        if isinstance(state, dict) and state.get("validate"):
            return self._ok(state["validate"], -1, 0.0, started)

        # Open the slider panel (radar button), then detect the gap.
        if isinstance(state, dict) and isinstance(state.get("radar"), dict):
            r = state["radar"]
            try:
                await page.click_xy(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2, humanize=True)
            except Exception:
                await page.eval_js("(() => { const b=document.querySelector('.geetest_radar_btn'); if(b) b.click(); })()")

        gap = await self._await_gap(page)
        if not gap.ok or gap.button is None or gap.gap_left < 0:
            return self._fail(challenge, f"gap detection failed ({gap.reason})", started)

        bx = gap.button["x"] + gap.button["width"] / 2
        by = gap.button["y"] + gap.button["height"] / 2
        await self._drag(page, bx, by, gap.drag_css)

        validate = await self._poll_validate(page)
        if validate:
            return self._ok(validate, gap.gap_left, gap.drag_css, started)

        # Geometry solved; the session was rejected behaviourally.
        return SolveResult(
            status=SolveStatus.FAILED, family=Family.GEETEST,
            error="puzzle aligned, but GeeTest v3 behavioural layer rejected the session "
                  "(no validate token) — anti-bot track, not the slider solver",
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "geetest-slide", "route": "anti-bot", "reason": "behavioural",
                      "gap_left": gap.gap_left, "drag_css": round(gap.drag_css, 1)},
        )

    async def _await_gap(self, page: Any) -> GeetestGap:
        deadline = time.time() + self.settle_s
        last = GeetestGap(ok=False, reason="timeout")
        while time.time() < deadline:
            last = parse_gap(await page.eval_js(GEETEST_DETECT_JS))
            if last.ok and last.button is not None and last.gap_left >= 0:
                return last
            await asyncio.sleep(0.4)
        return last

    async def _drag(self, page: Any, bx: float, by: float, distance: float, *, seed: int = 23) -> None:
        await page.dispatch_mouse_event("mouseMoved", bx, by)
        await page.dispatch_mouse_event("mousePressed", bx, by, button="left", click_count=1)
        await asyncio.sleep(0.12)
        last = (bx, by)
        for x, y in human_drag_path(bx, by, distance, seed=seed):
            await page.dispatch_mouse_event("mouseMoved", x, y)
            await asyncio.sleep(0.012 + random.Random(int(x)).uniform(0.0, 0.02))
            last = (x, y)
        await asyncio.sleep(0.08)
        await page.dispatch_mouse_event("mouseReleased", last[0], last[1], button="left", click_count=1)

    async def _poll_validate(self, page: Any) -> str:
        deadline = time.time() + self.result_wait_s
        while time.time() < deadline:
            state = await page.eval_js(GEETEST_STATE_JS)
            if isinstance(state, dict) and state.get("validate"):
                return str(state["validate"])
            await asyncio.sleep(0.4)
        return ""

    def _ok(self, validate, gap_left, drag, started) -> SolveResult:
        return SolveResult(
            status=SolveStatus.SOLVED, family=Family.GEETEST,
            solution=TokenSolution(str(validate)), solved_by=SolvedBy.LOCAL, vendor="geetest",
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "geetest-slide", "gap_left": gap_left, "drag_css": round(drag, 1)},
        )

    def _fail(self, challenge, error, started) -> SolveResult:
        return SolveResult(
            status=SolveStatus.FAILED, family=Family.GEETEST, error=error,
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "geetest-slide"},
        )
