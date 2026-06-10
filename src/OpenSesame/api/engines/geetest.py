"""GeeTest v3 + v4 slider — gap detection + humanized drag, one engine.

Both GeeTest generations render the slider in the *main document* and the
geometry is solved the same way (find the notch, drag the piece there with a
human-like accelerate / overshoot / settle curve); they differ only in how the
puzzle is exposed, so the engine detects the version and routes.

**v3 "fullpage"** uses three same-origin ``<canvas>`` elements —
``geetest_canvas_fullbg`` (complete image), ``geetest_canvas_bg`` (with the
notch), ``geetest_canvas_slice`` (the piece) — so the gap is a column-wise diff
of fullbg vs bg via ``getImageData``. Success mints ``geetest_validate``.

**v4** opens a popup (``.geetest_btn_click`` → ``.geetest_box``) whose background
and piece are CSS ``background-image`` PNGs served from ``static.geetest.com``.
There is no "full" reference, so the gap is found by edge-template-matching the
piece silhouette against the background (images fetched over HTTP). Two v4
specifics matter: the popup *animates into place*, so we wait for the slider
button position to settle before reading geometry (otherwise the drag targets a
stale, possibly off-screen point); and success is the ``geetest_success`` state
(e.g. "1.4 s. You beat 98% of users").

In both, the geometry is deterministic; GeeTest additionally scores the *session*
behaviourally (its ``gct`` tracker), which on an automated headless session is
intermittent — so the live examples reload-and-retry. A persistent reject is the
anti-bot track (CAS-180/182's behavioural moat), reported with ``route``.
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


# --- v4 -------------------------------------------------------------------

# v4 if the popup trigger / global init function is present (v3 has neither).
GEETEST_VERSION_JS = (
    "(() => (window.initGeetest4 || document.querySelector('.geetest_btn_click')) "
    "? 'v4' : (document.querySelector('.geetest_radar_btn, .geetest_holder') ? 'v3' : 'none'))()"
)
GEETEST_V4_OPEN_JS = (
    "(() => { const b = document.querySelector('.geetest_btn_click'); "
    "if (!b) return false; b.click(); return true; })()"
)
# Read the puzzle geometry + image URLs from the (settled) popup.
GEETEST_V4_GEO_JS = r"""
(() => {
  const url = el => { if (!el) return null;
    const m = (getComputedStyle(el).backgroundImage || '').match(/url\(["']?([^"')]+)/); return m ? m[1] : null; };
  const R = el => { if (!el) return null; const r = el.getBoundingClientRect();
    return {x: r.left, y: r.top, width: r.width, height: r.height}; };
  return {
    bg_url: url(document.querySelector('.geetest_bg')),
    slice_url: url(document.querySelector('.geetest_slice_bg')),
    bg: R(document.querySelector('.geetest_bg')),
    slice: R(document.querySelector('.geetest_slice')),
    button: R(document.querySelector('.geetest_slider .geetest_btn')),
  };
})()
"""
# v4 outcome: success carries a "geetest_success" state; a wrong slide shows
# "Please try again" in the result tips.
GEETEST_V4_STATE_JS = r"""
(() => {
  const rt = document.querySelector('.geetest_result_tips');
  const tips = rt ? (rt.innerText || '').trim() : '';
  return {
    tips: tips,
    success: !!document.querySelector('[class*="geetest_success"]')
             || /you beat \d+% of users|s\. you beat/i.test(tips),
    retry: /try again/i.test(tips),
  };
})()
"""


def detect_gap_v4(bg_png: bytes, slice_png: bytes, bg_rect: dict, slice_rect: dict) -> float:
    """Slice travel needed (displayed px) to seat the piece in the notch.

    Edge-template-matches the piece silhouette (from the slice's alpha) against
    the background's gradient along the piece's y-band. numpy/PIL are imported
    lazily so the module loads without them.
    """

    from io import BytesIO

    import numpy as np
    from PIL import Image

    bg = np.asarray(Image.open(BytesIO(bg_png)).convert("L")).astype(float)
    sl = np.asarray(Image.open(BytesIO(slice_png)).convert("RGBA"))
    h, w = bg.shape
    mask = sl[:, :, 3] > 16
    ys, xs = np.where(mask)
    top, bot, left, right = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    pw, ph = right - left + 1, bot - top + 1
    # Silhouette edge from the FULL slice mask (so a piece that fills its bounding
    # box still has a boundary), then crop to the piece.
    full = mask.astype(float)
    fe = np.zeros_like(full)
    fe[1:, :] += np.abs(full[1:, :] - full[:-1, :])
    fe[:, 1:] += np.abs(full[:, 1:] - full[:, :-1])
    pe = (fe[top:bot + 1, left:right + 1] > 0).astype(float)
    gx = np.zeros_like(bg)
    gy = np.zeros_like(bg)
    gx[:, 1:-1] = bg[:, 2:] - bg[:, :-2]
    gy[1:-1, :] = bg[2:, :] - bg[:-2, :]
    bg_edge = np.hypot(gx, gy)
    y0 = int(round((slice_rect["y"] - bg_rect["y"]) * h / bg_rect["height"])) + top
    band = slice(max(0, y0), min(h, y0 + ph))
    best_x, best = -1, -1.0
    for x in range(left + pw, w - pw):
        region = bg_edge[band, x:x + pw]
        score = float((region * pe[:region.shape[0], :]).sum())
        if score > best:
            best, best_x = score, x
    scale = bg_rect["width"] / w
    return max(0.0, (best_x - left) * scale)


async def _fetch_bytes(urls: list[str]) -> list[bytes]:
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        out = []
        for u in urls:
            out.append((await client.get(u)).content)
        return out


class GeetestSlideEngine:
    """GeeTest v3 + v4 slider: gap detection + humanized drag."""

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
        version = str(await page.eval_js(GEETEST_VERSION_JS) or "")
        if version == "v4":
            return await self._solve_v4(challenge, page)
        return await self._solve_v3(challenge, page)

    async def _solve_v3(self, challenge: Challenge, page: Any) -> SolveResult:
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

    # -- v4 ---------------------------------------------------------------

    async def _solve_v4(self, challenge: Challenge, page: Any) -> SolveResult:
        started = time.time()
        await page.eval_js(GEETEST_V4_OPEN_JS)
        geo = await self._v4_wait_stable(page)
        if not geo or not geo.get("bg_url") or not geo.get("button"):
            return self._fail(challenge, "v4 puzzle did not open / no slider button", started)
        try:
            bg_png, slice_png = await _fetch_bytes([geo["bg_url"], geo["slice_url"]])
            distance = detect_gap_v4(bg_png, slice_png, geo["bg"], geo["slice"])
        except Exception as exc:
            return self._fail(challenge, f"v4 gap detection failed: {type(exc).__name__}: {exc}", started)

        btn = geo["button"]
        bx = btn["x"] + btn["width"] / 2
        by = btn["y"] + btn["height"] / 2
        await self._drag(page, bx, by, distance, seed=int(started) % 97 + 1)

        state = await self._poll_v4_state(page)
        if state.get("success"):
            return SolveResult(
                status=SolveStatus.SOLVED, family=Family.GEETEST,
                solution=None, solved_by=SolvedBy.LOCAL, vendor="geetest",
                timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
                metadata={"strategy": "geetest-v4-slide", "drag_css": round(distance, 1),
                          "result": state.get("tips")},
            )
        return SolveResult(
            status=SolveStatus.FAILED, family=Family.GEETEST,
            error="v4 piece dragged to the detected gap, but GeeTest rejected the slide "
                  f"({state.get('tips') or 'no result'}) — wrong gap or behavioural reject; retry",
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "geetest-v4-slide", "drag_css": round(distance, 1),
                      "route": "anti-bot", "result": state.get("tips")},
        )

    async def _v4_wait_stable(self, page: Any) -> dict[str, Any] | None:
        """The popup animates in; wait until the slider button x stops moving."""

        deadline = time.time() + self.settle_s
        last_x: float | None = None
        geo: dict[str, Any] | None = None
        while time.time() < deadline:
            geo = await page.eval_js(GEETEST_V4_GEO_JS)
            btn = geo.get("button") if isinstance(geo, dict) else None
            if isinstance(btn, dict) and btn.get("width", 0) > 10 and geo.get("bg_url"):
                cur = round(btn["x"])
                if last_x is not None and abs(cur - last_x) < 2:
                    return geo
                last_x = cur
            await asyncio.sleep(0.3)
        return geo

    async def _poll_v4_state(self, page: Any) -> dict[str, Any]:
        deadline = time.time() + self.result_wait_s
        state: dict[str, Any] = {}
        while time.time() < deadline:
            state = await page.eval_js(GEETEST_V4_STATE_JS) or {}
            if isinstance(state, dict) and (state.get("success") or state.get("retry")):
                return state
            await asyncio.sleep(0.4)
        return state if isinstance(state, dict) else {}

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
