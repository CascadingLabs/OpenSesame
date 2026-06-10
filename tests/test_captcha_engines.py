"""Unit tests for the MTCaptcha / GeeTest / Rotate engines (pure helpers + wiring)."""

from __future__ import annotations

import base64

from OpenSesame.api.defaults import default_solver
from OpenSesame.api.engines.geetest import GeetestGap, human_drag_path, parse_gap
from OpenSesame.api.engines.mtcaptcha import decode_data_url, plausible_answer
from OpenSesame.api.engines.rotate import center_of_pass_window
from OpenSesame.api.result import Family
from OpenSesame.api.policy import SolverPolicy


def test_default_solver_registers_new_engines() -> None:
    solver = default_solver(SolverPolicy.auto_only(allow_sites=["2captcha.com"]))
    engines = solver._engines  # noqa: SLF001 - test reaches into wiring intentionally
    assert engines[Family.MTCAPTCHA].family is Family.MTCAPTCHA
    assert engines[Family.GEETEST].family is Family.GEETEST
    assert engines[Family.ROTATE].family is Family.ROTATE


def test_decode_data_url_handles_gif_and_png() -> None:
    payload = b"GIF89a\x00\x00bytes"
    url = "data:image/gif;base64," + base64.b64encode(payload).decode()
    assert decode_data_url(url) == payload
    assert decode_data_url("https://x/y.png") is None


def test_plausible_answer_bounds() -> None:
    assert plausible_answer("aB3xZ") is True
    assert plausible_answer("ab") is False
    assert plausible_answer("waytoolonganswer") is False


def test_parse_gap_drag_distance() -> None:
    gap = parse_gap({
        "ok": True, "gapLeft": 180, "sliceLeft": 8, "scale": 258.0 / 260.0,
        "slider_button": {"x": 100.0, "y": 400.0, "width": 40.0, "height": 40.0},
    })
    assert gap.ok is True
    assert abs(gap.drag_css - (172 * 258.0 / 260.0)) < 0.01


def test_parse_gap_failure() -> None:
    assert parse_gap({"ok": False, "reason": "no-canvas"}).ok is False
    assert parse_gap("nope") == GeetestGap(ok=False, reason="unreadable")


def test_human_drag_path_overshoots_then_settles() -> None:
    path = human_drag_path(120.0, 410.0, 168.0, steps=30, seed=5)
    assert abs(path[0][0] - 120.0) < 0.001
    assert abs(path[-1][0] - (120.0 + 168.0)) < 0.5
    assert max(x for x, _ in path) > 120.0 + 168.0


def test_center_of_pass_window() -> None:
    # 2Captcha demo accepts 165-345deg (indices 11..23); centre is 255deg (index 17).
    assert center_of_pass_window(list(range(11, 24)), total=24) == 17
    assert center_of_pass_window([22, 23, 0, 1, 2], total=24) == 0
    assert center_of_pass_window([], total=24) is None
