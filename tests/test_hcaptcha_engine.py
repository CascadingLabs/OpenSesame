"""hCaptcha canvas engine — scripted DOM, no live browser.

The challenge is painted to a ``<canvas>`` with a zero-size a11y mirror, so the
engine screenshots it, asks the VLM provider to *ground* a normalized point, maps
it onto the page bbox, and humanized-clicks the canvas. These tests fake the page
(frame-scoped eval + screenshot + click_xy) and the ``vlm`` provider so the whole
round loop runs deterministically.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.defaults import default_solver
from OpenSesame.api.engines._hcaptcha_dom import page_bbox
from OpenSesame.api.engines.hcaptcha import HcaptchaEngine
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelRegistry
from OpenSesame.api.result import Family, SolvedBy, SolveStatus
from OpenSesame.api.solver import _OUT_OF_SCOPE_ROUTES


def run(coro):
    return asyncio.run(coro)


POLICY = SolverPolicy(allow_sites=["accounts.hcaptcha.com"], audit_log=None)


class FakeReasoner:
    """Stands in for the local Qwen2.5-VL grounding provider."""

    model_id = "fake/vlm"
    device = "cpu"

    def __init__(self, point=(0.5, 0.6, 1.0)) -> None:
        self.point = point
        self.calls: list[tuple[list[str], str]] = []

    def locate_burst(self, frame_paths: list[str], *, instruction: str):
        self.calls.append((frame_paths, instruction))
        return self.point


class FakeHcaptchaPage:
    """Scripts the canvas flow: open → state → screenshot → click → submit → token."""

    def __init__(
        self,
        *,
        prompt: str = "Choose the card that shows a different animal",
        preset_token: str = "",
        rounds_needed: int = 1,
        reload_available: bool = True,
    ) -> None:
        self.prompt = prompt
        self._token = preset_token
        self.rounds_needed = rounds_needed
        self.reload_available = reload_available
        self.opened = False
        self.submits = 0
        self.reloads = 0
        self.clicks: list[tuple[float, float, bool]] = []
        self.shots: list[tuple[str, tuple]] = []

    # -- parent document reads -------------------------------------------
    async def eval_js(self, js: str):
        if "h-captcha-response" in js:
            return self._token
        if "getBoundingClientRect" in js or "frame=challenge" in js:
            return {"left": 33.0, "top": 249.0, "width": 300.0, "height": 150.0}
        return None

    # -- frame-scoped drives ---------------------------------------------
    async def eval_js_in_frame(self, pattern: str, js: str):
        if "frame=checkbox" in pattern:
            self.opened = True            # checkbox opens the challenge
            return True
        # challenge frame — route on each snippet's unique marker. STATE_JS also
        # mentions `.button-submit` (it reads the label), so match "prompt" first.
        if "prompt" in js:                # STATE_JS
            if not self.opened:
                return {"present": False}
            return {
                "present": True, "prompt": self.prompt,
                "canvas": {"left": 10.0, "top": 10.0, "width": 500.0, "height": 470.0},
                "submit": "Next", "crumbs": 2,
            }
        if "reload" in js or "refresh" in js:   # CLICK_RELOAD_JS
            if not self.reload_available:
                return False
            self.reloads += 1
            return True
        if "button-submit" in js:               # CLICK_SUBMIT_JS
            self.submits += 1
            if self.submits >= self.rounds_needed:
                self._token = "P0_hcaptcha_TOKEN"
            return True
        if "canvas" in js:                      # CANVAS_READY_JS
            return self.opened
        return None

    async def screenshot(self, path: str, bbox=None):
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")   # bytes only; the reasoner is faked
        self.shots.append((path, bbox))

    async def click_xy(self, x: float, y: float, humanize: bool = False):
        self.clicks.append((x, y, humanize))


def _solve(page, reasoner=None, **engine_kw):
    reg = ModelRegistry()
    reg.register_factory("vlm", lambda key: reasoner or FakeReasoner())
    ch = Challenge(
        family=Family.HCAPTCHA, url="https://accounts.hcaptcha.com/demo",
        host="accounts.hcaptcha.com", vendor_kind="hcaptcha",
    )
    # 1-frame burst, no inter-frame sleep — keeps the unit test fast + numpy-free
    # (a 1-frame burst skips the composite path in the provider).
    engine_kw = {"burst_frames": 1, "burst_interval_s": 0.0, **engine_kw}
    return run(HcaptchaEngine(**engine_kw).solve(ch, page, registry=reg, policy=POLICY))


# -- happy path -----------------------------------------------------------

def test_solves_canvas_single_select() -> None:
    page = FakeHcaptchaPage(rounds_needed=2)
    reasoner = FakeReasoner(point=(0.5, 0.6, 1.0))
    result = _solve(page, reasoner)

    assert result.ok and result.token == "P0_hcaptcha_TOKEN"
    assert result.solved_by is SolvedBy.LOCAL and result.vendor == "hcaptcha"
    assert result.metadata["strategy"] == "canvas-vlm"
    assert len(result.metadata["rounds"]) == 2            # looped both sub-challenges
    # The canvas was clicked at the model's point mapped onto the page bbox,
    # humanized. bbox = iframe(33,249) + canvas(10,10) = (43,259,500,470).
    x, y, humanized = page.clicks[0]
    assert (round(x), round(y), humanized) == (43 + round(0.5 * 500), 259 + round(0.6 * 470), True)
    assert reasoner.calls and reasoner.calls[0][1] == page.prompt   # prompt forwarded to VLM


def test_already_solved_returns_token_without_clicking() -> None:
    page = FakeHcaptchaPage(preset_token="ALREADY_PASSED")
    result = _solve(page)
    assert result.ok and result.token == "ALREADY_PASSED"
    assert page.clicks == [] and page.submits == 0        # nothing to do


# -- task-type guard ------------------------------------------------------

def test_unsupported_task_reloads_then_fails() -> None:
    page = FakeHcaptchaPage(
        prompt="Please click each image containing a train", reload_available=False,
    )
    result = _solve(page)
    assert result.status is SolveStatus.FAILED
    assert "unsupported hcaptcha task" in result.error
    assert page.clicks == []                              # never clicked a card


def test_multi_select_prompt_is_deferred() -> None:
    assert HcaptchaEngine._is_single_select("Choose the card that shows a different animal")
    assert not HcaptchaEngine._is_single_select("Please click each image containing a train")
    assert not HcaptchaEngine._is_single_select("Select all the buses")
    assert not HcaptchaEngine._is_single_select("")


# -- geometry helper ------------------------------------------------------

def test_page_bbox_adds_iframe_offset_to_canvas_rect() -> None:
    bbox = page_bbox(
        {"left": 33, "top": 249, "width": 300, "height": 150},
        {"left": 10, "top": 10, "width": 500, "height": 470},
    )
    assert bbox == (43, 259, 500, 470)
    assert page_bbox(None, {"left": 1}) is None           # missing rect → no crop


# -- routing: hCaptcha is a solve target now ------------------------------

def test_hcaptcha_is_not_routed_out_of_scope() -> None:
    assert Family.HCAPTCHA not in _OUT_OF_SCOPE_ROUTES
    solver = default_solver(SolverPolicy.auto_only(allow_sites=["accounts.hcaptcha.com"]))
    assert Family.HCAPTCHA in solver._engines
    assert isinstance(solver._engines[Family.HCAPTCHA], HcaptchaEngine)
