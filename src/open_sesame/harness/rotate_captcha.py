"""Live RotateCaptcha solver (2Captcha rotate demo).

The 2Captcha rotate demo (``2captcha.com/demo/rotatecaptcha``) shows one image
rotated away from upright and two arrow buttons that step the rotation by 15°.
There is no token: validation is client-side — "Check" reports
*"Captcha is passed successfully!"* when the image is upright (within tolerance)
or *"Incorrect captcha angle, please try again."* otherwise. The demo's own
instruction is literally "click the rotation arrows until the image reaches the
desired angle, then click Check".

This is a direct-answer captcha whose answer is a rotation. A production rotate
captcha (single-shot submit) needs a trained angle predictor; this widget
instead exposes a pass/fail oracle, so the honest, reliable solver is a
**closed-loop rotate-and-verify search**: step the rotation and re-check until
the page confirms upright. General to any rotate-until-correct widget, no model
and no per-asset constant.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


# Read the rotate widget: current applied angle, last verdict, control rects.
ROTATE_STATE_JS = r"""
(() => {
  const img = document.querySelector('img[alt="rotatecaptcha example"]');
  let angle = null;
  if (img) {
    const m = (img.getAttribute('style') || '').match(/rotate\((-?\d+(?:\.\d+)?)deg\)/);
    if (m) angle = parseFloat(m[1]);
  }
  const body = document.body.innerText || '';
  const rr = document.querySelector('._rotateRightBtn_12mm0_17, [class*="rotateRightBtn"]');
  const rl = document.querySelector('._rotateLeftBtn_12mm0_18, [class*="rotateLeftBtn"]');
  const check = Array.from(document.querySelectorAll('button')).find(
    b => /^check$/i.test((b.innerText || '').trim())
  );
  const rect = el => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: r.left, y: r.top, width: r.width, height: r.height};
  };
  return {
    angle: angle,
    passed: /passed successfully/i.test(body),
    failed: /incorrect captcha angle/i.test(body),
    has_controls: !!(rr && rl && check),
    rotate_right: rect(rr),
    rotate_left: rect(rl),
    check: rect(check),
    img: rect(img),
  };
})()
"""

CLICK_ROTATE_RIGHT_JS = (
    "(() => { const b = document.querySelector('._rotateRightBtn_12mm0_17, "
    "[class*=\"rotateRightBtn\"]'); if (!b) return false; b.click(); return true; })()"
)
CLICK_ROTATE_LEFT_JS = (
    "(() => { const b = document.querySelector('._rotateLeftBtn_12mm0_18, "
    "[class*=\"rotateLeftBtn\"]'); if (!b) return false; b.click(); return true; })()"
)
CLICK_CHECK_JS = (
    "(() => { const b = Array.from(document.querySelectorAll('button'))"
    ".find(x => /^check$/i.test((x.innerText || '').trim())); "
    "if (!b) return false; b.click(); return true; })()"
)

STEP_DEGREES = 15


@dataclass(frozen=True)
class RotateState:
    angle: float | None
    passed: bool
    failed: bool
    has_controls: bool

    @classmethod
    def parse(cls, raw: Any) -> RotateState:
        if not isinstance(raw, dict):
            return cls(angle=None, passed=False, failed=False, has_controls=False)
        angle = raw.get("angle")
        return cls(
            angle=float(angle) if isinstance(angle, (int, float)) else None,
            passed=bool(raw.get("passed")),
            failed=bool(raw.get("failed")),
            has_controls=bool(raw.get("has_controls")),
        )


@dataclass(frozen=True)
class RotateAttempt:
    step: int
    angle: float | None
    passed: bool


@dataclass(frozen=True)
class RotateResult:
    solved: bool
    final_angle: float | None
    steps: int
    attempts: tuple[RotateAttempt, ...]
    elapsed_ms: float
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "solved": self.solved,
            "final_angle": self.final_angle,
            "steps": self.steps,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error": self.error,
            "attempts": [
                {"step": a.step, "angle": a.angle, "passed": a.passed} for a in self.attempts
            ],
        }


async def read_rotate_state(page: Any) -> RotateState:
    return RotateState.parse(await page.eval_js(ROTATE_STATE_JS))


async def click_check(page: Any) -> None:
    await page.eval_js(CLICK_CHECK_JS)


async def rotate_step(page: Any, *, direction: str = "right") -> None:
    js = CLICK_ROTATE_RIGHT_JS if direction == "right" else CLICK_ROTATE_LEFT_JS
    await page.eval_js(js)


def center_of_pass_window(passing_indices: list[int], total: int = 24) -> int | None:
    """Index of the middle of the largest contiguous passing run (circular)."""

    if not passing_indices:
        return None
    passing = set(passing_indices)
    if len(passing) == total:
        return passing_indices[len(passing_indices) // 2]
    # Rotate the ring so it starts just after a gap, then find the longest run.
    start = next(i for i in range(total) if i not in passing)
    order = [(start + k) % total for k in range(total)]
    best_run: list[int] = []
    run: list[int] = []
    for idx in order:
        if idx in passing:
            run.append(idx)
            if len(run) > len(best_run):
                best_run = run[:]
        else:
            run = []
    if not best_run:
        return None
    return best_run[len(best_run) // 2]


async def solve_rotate_captcha(
    page: Any,
    *,
    direction: str = "right",
    mode: str = "center",
    max_steps: int = 24,
    settle: float = 0.55,
    on_event: Any = None,
) -> RotateResult:
    """Rotate-and-verify until the demo reports the image is upright.

    ``mode="first"`` stops at the first accepted angle. ``mode="center"`` sweeps
    the rotation, maps the accepted window, and settles on its centre so the
    image ends genuinely upright (the demo's tolerance is wide).
    """

    started = time.perf_counter()
    attempts: list[RotateAttempt] = []

    def emit(message: str) -> None:
        if on_event is not None:
            on_event(message)

    try:
        if mode == "first":
            for step in range(max_steps + 1):
                await click_check(page)
                await asyncio.sleep(settle)
                state = await read_rotate_state(page)
                attempts.append(RotateAttempt(step, state.angle, state.passed))
                emit(f"step {step}: angle={state.angle}deg -> " + ("PASS — upright" if state.passed else "not upright"))
                if state.passed:
                    return RotateResult(True, state.angle, step, tuple(attempts), _ms(started))
                await rotate_step(page, direction="right")
                await asyncio.sleep(0.3)
            return RotateResult(False, None, max_steps, tuple(attempts), _ms(started))

        # mode == "center": sweep one full turn (right), record the verdict at
        # each 15° step, then rotate back to the centre of the accepted window.
        passing: list[int] = []
        for step in range(max_steps):
            await click_check(page)
            await asyncio.sleep(settle)
            state = await read_rotate_state(page)
            attempts.append(RotateAttempt(step, state.angle, state.passed))
            if state.passed:
                passing.append(step)
            emit(f"sweep {step}: angle={state.angle}deg -> " + ("upright" if state.passed else "—"))
            await rotate_step(page, direction="right")
            await asyncio.sleep(0.25)

        center = center_of_pass_window(passing, total=max_steps)
        if center is None:
            return RotateResult(False, None, max_steps, tuple(attempts), _ms(started))

        # After the sweep the image is back at index 0 (full turn). Rotate left to
        # the centre index (fewer clicks than continuing right).
        back = (max_steps - center) % max_steps
        emit(f"window centre at {center * STEP_DEGREES}deg; settling there")
        for _ in range(back):
            await rotate_step(page, direction="left")
            await asyncio.sleep(0.18)
        await click_check(page)
        await asyncio.sleep(settle)
        final = await read_rotate_state(page)
        attempts.append(RotateAttempt(max_steps, final.angle, final.passed))
        emit(f"final: angle={final.angle}deg -> " + ("PASS — upright" if final.passed else "not upright"))
        return RotateResult(final.passed, final.angle, max_steps, tuple(attempts), _ms(started))
    except Exception as exc:  # pragma: no cover - live browser path
        return RotateResult(False, None, len(attempts), tuple(attempts), _ms(started), error=f"{type(exc).__name__}: {exc}")


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
