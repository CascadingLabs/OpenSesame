"""Live GeeTest v3 slider solver (CAS-182 / shared slider toolkit CAS-180).

The 2Captcha GeeTest demo (``2captcha.com/demo/geetest``) runs GeeTest v3
"fullpage" in the *main document* (no iframe): the puzzle is three same-origin
``<canvas>`` elements —

* ``geetest_canvas_fullbg``  — the complete background image,
* ``geetest_canvas_bg``      — the same image with the puzzle notch cut out,
* ``geetest_canvas_slice``   — the draggable puzzle piece.

Because they are same-origin we can read their pixels with ``getImageData`` and
locate the gap by a column-wise diff of fullbg vs bg — no model needed. The
remaining moat is behavioural: a pixel-perfect offset still fails if the drag
trajectory is too robotic, so we drag with a human-like accelerate / overshoot /
settle curve via CDP mouse events. Success mints the real GeeTest triple
(``geetest_challenge`` / ``geetest_validate`` / ``geetest_seccode``).
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any


# Lightweight state: is the radar button up, is the puzzle solved yet.
GEETEST_STATE_JS = r"""
(() => {
  const holder = document.querySelector('.geetest_holder');
  const radar = document.querySelector('.geetest_radar_btn');
  const validate = document.querySelector('input[name="geetest_validate"]');
  const rr = radar ? radar.getBoundingClientRect() : null;
  return {
    has_radar: !!radar,
    radar: rr ? {x: rr.left, y: rr.top, width: rr.width, height: rr.height} : null,
    holder_class: holder ? holder.className : '',
    validate: validate ? (validate.value || '') : '',
    success: !!document.querySelector('.geetest_holder.geetest_success, .geetest_success_radar_tip'),
  };
})()
"""


# Locate the gap by diffing the bg (notched) canvas against the fullbg (complete)
# canvas column-by-column, in canvas pixels. Also reads the slice piece's initial
# left edge and the displayed/native scale so the parent can compute a drag in
# CSS pixels.
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
      s += Math.abs(bgd[i] - fud[i]) + Math.abs(bgd[i + 1] - fud[i + 1]) + Math.abs(bgd[i + 2] - fud[i + 2]);
    }
    colDiff[x] = s;
  }
  let maxd = 0;
  for (let x = 0; x < W; x++) if (colDiff[x] > maxd) maxd = colDiff[x];
  if (maxd <= 0) return {ok: false, reason: 'no-diff'};
  const thr = maxd * 0.35;
  // First sustained high-diff run (left to right), skipping the far-left strip
  // where the slice piece itself sits.
  let gapLeft = -1, gapRight = -1, miss = 0;
  for (let x = 4; x < W; x++) {
    if (colDiff[x] > thr) {
      if (gapLeft < 0) gapLeft = x;
      gapRight = x;
      miss = 0;
    } else if (gapLeft >= 0) {
      if (++miss > 8) break;
    }
  }

  let sliceLeft = 0;
  if (slice) {
    try {
      const W2 = slice.width, H2 = slice.height;
      const sd = slice.getContext('2d').getImageData(0, 0, W2, H2).data;
      outer: for (let x = 0; x < W2; x++) {
        for (let y = 0; y < H2; y++) {
          if (sd[(y * W2 + x) * 4 + 3] > 16) { sliceLeft = x; break outer; }
        }
      }
    } catch (e) {}
  }

  const dispW = bg.getBoundingClientRect().width || W;
  let btn = null;
  if (sliderBtn) {
    const r = sliderBtn.getBoundingClientRect();
    btn = {x: r.left, y: r.top, width: r.width, height: r.height};
  }
  return {
    ok: true, gapLeft, gapRight, sliceLeft,
    canvasW: W, dispW, scale: dispW / W, slider_button: btn,
  };
})()
"""


@dataclass(frozen=True)
class GeetestRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


@dataclass(frozen=True)
class GeetestGap:
    ok: bool
    gap_left: int = -1
    gap_right: int = -1
    slice_left: int = 0
    canvas_w: int = 0
    disp_w: float = 0.0
    scale: float = 1.0
    slider_button: GeetestRect | None = None
    reason: str = ""

    @property
    def drag_distance_css(self) -> float:
        """How far to drag the slider button, in displayed CSS pixels."""

        return max(0.0, (self.gap_left - self.slice_left) * self.scale)


def parse_gap(raw: Any) -> GeetestGap:
    if not isinstance(raw, dict) or not raw.get("ok"):
        reason = raw.get("reason", "unreadable") if isinstance(raw, dict) else "unreadable"
        return GeetestGap(ok=False, reason=str(reason))
    btn = raw.get("slider_button")
    return GeetestGap(
        ok=True,
        gap_left=int(raw.get("gapLeft", -1)),
        gap_right=int(raw.get("gapRight", -1)),
        slice_left=int(raw.get("sliceLeft", 0)),
        canvas_w=int(raw.get("canvasW", 0)),
        disp_w=float(raw.get("dispW", 0.0)),
        scale=float(raw.get("scale", 1.0)),
        slider_button=_rect(btn),
    )


def _rect(value: Any) -> GeetestRect | None:
    if not isinstance(value, dict):
        return None
    try:
        return GeetestRect(
            x=float(value["x"]),
            y=float(value["y"]),
            width=float(value["width"]),
            height=float(value["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def human_drag_path(
    start_x: float,
    start_y: float,
    distance: float,
    *,
    steps: int = 34,
    seed: int = 19,
) -> tuple[tuple[float, float], ...]:
    """Human-like horizontal drag: accelerate, overshoot, settle back.

    GeeTest scores the trajectory, so a constant-velocity slide fails even with
    the right offset. This eases in/out, overshoots a few px past the target,
    then corrects back, with light vertical tremor.
    """

    rng = random.Random(seed)
    overshoot = max(2.0, distance * 0.06) + rng.uniform(1.0, 4.0)
    peak = distance + overshoot
    points: list[tuple[float, float]] = []
    for index in range(steps):
        t = index / (steps - 1)
        # smootherstep easing
        ease = t * t * t * (t * (t * 6 - 15) + 10)
        x = start_x + peak * ease
        y = start_y + (rng.uniform(-1.3, 1.3) if 0 < index < steps - 1 else 0.0)
        points.append((x, y))
    settle = 7
    for index in range(1, settle + 1):
        t = index / settle
        x = start_x + peak + (distance - peak) * (t * t * (3 - 2 * t))
        y = start_y + rng.uniform(-0.8, 0.8)
        points.append((x, y))
    return tuple(points)


@dataclass(frozen=True)
class GeetestAttempt:
    index: int
    gap_left: int
    slice_left: int
    drag_css: float
    validate: str | None
    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def solved(self) -> bool:
        return bool(self.validate)


@dataclass(frozen=True)
class GeetestResult:
    solved: bool
    validate: str | None
    attempts: tuple[GeetestAttempt, ...]
    elapsed_ms: float
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "solved": self.solved,
            "validate": self.validate,
            "validate_length": len(self.validate) if self.validate else 0,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error": self.error,
            "attempts": [
                {
                    "index": a.index,
                    "gap_left": a.gap_left,
                    "slice_left": a.slice_left,
                    "drag_css": round(a.drag_css, 1),
                    "solved": a.solved,
                    "signals": list(a.signals),
                }
                for a in self.attempts
            ],
        }


async def read_geetest_state(page: Any) -> dict[str, Any]:
    raw = await page.eval_js(GEETEST_STATE_JS)
    return raw if isinstance(raw, dict) else {}


async def detect_gap(page: Any) -> GeetestGap:
    return parse_gap(await page.eval_js(GEETEST_DETECT_JS))


async def open_geetest_panel(page: Any, state: dict[str, Any]) -> bool:
    """Click the radar button to reveal the slider puzzle."""

    radar = _rect(state.get("radar"))
    if radar is None:
        return False
    cx, cy = radar.center
    try:
        await page.click_xy(cx, cy, humanize=True)
        return True
    except Exception:
        try:
            await page.eval_js("(() => { const b=document.querySelector('.geetest_radar_btn'); if(b) b.click(); })()")
            return True
        except Exception:
            return False


async def drag_slider(page: Any, button: GeetestRect, distance: float, *, seed: int) -> None:
    """Press the slider button and drag it ``distance`` CSS px with a human curve."""

    x0, y0 = button.center
    await page.dispatch_mouse_event("mouseMoved", x0, y0)
    await page.dispatch_mouse_event("mousePressed", x0, y0, button="left", click_count=1)
    await asyncio.sleep(0.12 + random.Random(seed).uniform(0.0, 0.08))
    last = (x0, y0)
    for x, y in human_drag_path(x0, y0, distance, seed=seed):
        await page.dispatch_mouse_event("mouseMoved", x, y)
        # Variable cadence: a touch slower near the end as a human homes in.
        await asyncio.sleep(0.012 + random.Random(int(x)).uniform(0.0, 0.02))
        last = (x, y)
    await asyncio.sleep(0.08)
    await page.dispatch_mouse_event("mouseReleased", last[0], last[1], button="left", click_count=1)


async def solve_geetest_slider(
    page: Any,
    *,
    max_attempts: int = 5,
    settle_wait: float = 8.0,
    result_wait: float = 6.0,
    on_event: Any = None,
) -> GeetestResult:
    """Drive a live GeeTest v3 slider to a minted validate token."""

    started = time.perf_counter()
    attempts: list[GeetestAttempt] = []

    def emit(message: str) -> None:
        if on_event is not None:
            on_event(message)

    try:
        state = await read_geetest_state(page)
        if state.get("validate"):
            return GeetestResult(True, state["validate"], (), _ms(started))

        for index in range(1, max_attempts + 1):
            signals: list[str] = []
            state = await read_geetest_state(page)

            # Open / re-open the slider panel if only the radar button is showing.
            gap = await detect_gap(page)
            if not gap.ok or gap.slider_button is None:
                if await open_geetest_panel(page, state):
                    signals.append("opened-panel")
                gap = await _await_gap(page, timeout=settle_wait)

            if not gap.ok or gap.slider_button is None or gap.gap_left < 0:
                attempts.append(GeetestAttempt(index, gap.gap_left, gap.slice_left, 0.0, None, (*signals, f"detect-failed:{gap.reason}")))
                emit(f"attempt {index}: gap detection failed ({gap.reason})")
                await asyncio.sleep(1.0)
                continue

            distance = gap.drag_distance_css
            signals.append(f"gap_left={gap.gap_left} slice_left={gap.slice_left} drag={distance:.1f}")
            emit(f"attempt {index}: gap at x={gap.gap_left}, dragging {distance:.0f}px")
            await drag_slider(page, gap.slider_button, distance, seed=17 + index * 7)

            validate = None
            deadline = time.perf_counter() + result_wait
            while time.perf_counter() < deadline:
                st = await read_geetest_state(page)
                if st.get("validate"):
                    validate = st["validate"]
                    break
                await asyncio.sleep(0.4)

            attempts.append(GeetestAttempt(index, gap.gap_left, gap.slice_left, distance, validate, tuple(signals)))
            if validate:
                emit(f"attempt {index}: SOLVED — validate minted (len {len(validate)})")
                return GeetestResult(True, validate, tuple(attempts), _ms(started))

            emit(f"attempt {index}: no validate; GeeTest reships the puzzle")
            await asyncio.sleep(1.2)

        return GeetestResult(False, None, tuple(attempts), _ms(started))
    except Exception as exc:  # pragma: no cover - live browser path
        return GeetestResult(False, None, tuple(attempts), _ms(started), error=f"{type(exc).__name__}: {exc}")


async def _await_gap(page: Any, *, timeout: float) -> GeetestGap:
    deadline = time.perf_counter() + timeout
    last = GeetestGap(ok=False, reason="timeout")
    while time.perf_counter() < deadline:
        last = await detect_gap(page)
        if last.ok and last.slider_button is not None and last.gap_left >= 0:
            return last
        await asyncio.sleep(0.4)
    return last


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
