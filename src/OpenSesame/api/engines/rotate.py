"""RotateCaptcha — rotate-to-upright, verified against the widget's own oracle.

The 2Captcha rotate demo shows one image rotated away from upright with two arrow
buttons that step the rotation by 15 degrees, and a "Check" button that reports
*"Captcha is passed successfully!"* when upright (within a wide tolerance) or
*"Incorrect captcha angle…"* otherwise. There is no token: the answer is a
rotation, applied in-session.

A production single-shot rotate captcha needs a trained angle predictor; this
widget instead exposes a pass/fail oracle and the demo's own instruction is
"rotate until upright, then Check". So the honest, reliable, model-free solver is
a **closed-loop rotate-and-verify search**: sweep the rotation, map the accepted
window, and settle on its centre so the image ends genuinely upright. Returns an
``AnswerSolution`` carrying the final angle.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    AnswerSolution,
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    Timing,
)

STEP_DEGREES = 15

ROTATE_STATE_JS = r"""
(() => {
  const img = document.querySelector('img[alt="rotatecaptcha example"]');
  let angle = null;
  if (img) {
    const m = (img.getAttribute('style') || '').match(/rotate\((-?\d+(?:\.\d+)?)deg\)/);
    if (m) angle = parseFloat(m[1]);
  }
  const body = document.body.innerText || '';
  return {
    angle: angle,
    passed: /passed successfully/i.test(body),
    failed: /incorrect captcha angle/i.test(body),
  };
})()
"""
CLICK_RIGHT_JS = (
    "(() => { const b = document.querySelector('._rotateRightBtn_12mm0_17, "
    "[class*=\"rotateRightBtn\"]'); if (!b) return false; b.click(); return true; })()"
)
CLICK_LEFT_JS = (
    "(() => { const b = document.querySelector('._rotateLeftBtn_12mm0_18, "
    "[class*=\"rotateLeftBtn\"]'); if (!b) return false; b.click(); return true; })()"
)
CLICK_CHECK_JS = (
    "(() => { const b = Array.from(document.querySelectorAll('button'))"
    ".find(x => /^check$/i.test((x.innerText || '').trim())); "
    "if (!b) return false; b.click(); return true; })()"
)


def center_of_pass_window(passing: list[int], total: int = 24) -> int | None:
    """Index of the middle of the largest contiguous passing run (circular)."""

    if not passing:
        return None
    seen = set(passing)
    if len(seen) == total:
        return passing[len(passing) // 2]
    start = next(i for i in range(total) if i not in seen)
    best: list[int] = []
    run: list[int] = []
    for k in range(total):
        idx = (start + k) % total
        if idx in seen:
            run.append(idx)
            if len(run) > len(best):
                best = run[:]
        else:
            run = []
    return best[len(best) // 2] if best else None


class RotateEngine:
    """RotateCaptcha: closed-loop rotate-and-verify; settle on the upright centre."""

    family = Family.ROTATE

    def __init__(self, *, mode: str = "center", max_steps: int = 24, settle_s: float = 0.55) -> None:
        self.mode = mode
        self.max_steps = max_steps
        self.settle_s = settle_s

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
        if self.mode == "first":
            return await self._solve_first(challenge, page, started)
        return await self._solve_center(challenge, page, started)

    async def _solve_first(self, challenge, page, started) -> SolveResult:
        for step in range(self.max_steps + 1):
            await page.eval_js(CLICK_CHECK_JS)
            await asyncio.sleep(self.settle_s)
            st = await page.eval_js(ROTATE_STATE_JS)
            if isinstance(st, dict) and st.get("passed"):
                return self._ok(st.get("angle"), step, started)
            await page.eval_js(CLICK_RIGHT_JS)
            await asyncio.sleep(0.3)
        return self._fail(challenge, "did not reach an accepted angle", started)

    async def _solve_center(self, challenge, page, started) -> SolveResult:
        passing: list[int] = []
        for step in range(self.max_steps):
            await page.eval_js(CLICK_CHECK_JS)
            await asyncio.sleep(self.settle_s)
            st = await page.eval_js(ROTATE_STATE_JS)
            if isinstance(st, dict) and st.get("passed"):
                passing.append(step)
            await page.eval_js(CLICK_RIGHT_JS)
            await asyncio.sleep(0.25)

        center = center_of_pass_window(passing, total=self.max_steps)
        if center is None:
            return self._fail(challenge, "no accepted angle in a full sweep", started)

        # The sweep returned to index 0; rotate left to the window centre.
        for _ in range((self.max_steps - center) % self.max_steps):
            await page.eval_js(CLICK_LEFT_JS)
            await asyncio.sleep(0.18)
        await page.eval_js(CLICK_CHECK_JS)
        await asyncio.sleep(self.settle_s)
        st = await page.eval_js(ROTATE_STATE_JS)
        if isinstance(st, dict) and st.get("passed"):
            return self._ok(st.get("angle"), self.max_steps, started,
                            window=[p * STEP_DEGREES for p in passing])
        return self._fail(challenge, "centre angle was rejected", started)

    def _ok(self, angle, steps, started, *, window: list[int] | None = None) -> SolveResult:
        md: dict[str, Any] = {"strategy": "rotate-verify", "final_angle": angle, "steps": steps}
        if window is not None:
            md["accepted_window_deg"] = window
        return SolveResult(
            status=SolveStatus.SOLVED, family=Family.ROTATE,
            solution=AnswerSolution(str(int(angle)) if angle is not None else ""),
            solved_by=SolvedBy.LOCAL, vendor="rotatecaptcha",
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata=md,
        )

    def _fail(self, challenge, error, started) -> SolveResult:
        return SolveResult(
            status=SolveStatus.FAILED, family=Family.ROTATE, error=error,
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "rotate-verify"},
        )
