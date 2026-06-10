"""Unit tests for RotateCaptcha pure helpers (no live browser)."""

from __future__ import annotations

from open_sesame.harness.rotate_captcha import (
    RotateAttempt,
    RotateResult,
    RotateState,
    center_of_pass_window,
)


def test_center_of_pass_window_contiguous() -> None:
    # 2Captcha demo accepts 165-345deg, i.e. indices 11..23; centre is 255deg.
    assert center_of_pass_window(list(range(11, 24)), total=24) == 17  # 17 * 15 = 255


def test_center_of_pass_window_wraps_around_zero() -> None:
    assert center_of_pass_window([22, 23, 0, 1, 2], total=24) == 0


def test_center_of_pass_window_empty_is_none() -> None:
    assert center_of_pass_window([], total=24) is None


def test_rotate_state_parse_reads_angle_and_verdict() -> None:
    state = RotateState.parse(
        {"angle": 180, "passed": True, "failed": False, "has_controls": True}
    )
    assert state.angle == 180.0
    assert state.passed is True
    assert state.has_controls is True


def test_rotate_state_parse_handles_missing_angle() -> None:
    state = RotateState.parse({"angle": None, "passed": False, "failed": True, "has_controls": True})
    assert state.angle is None
    assert state.failed is True


def test_rotate_state_parse_handles_garbage() -> None:
    state = RotateState.parse("nope")
    assert state.angle is None
    assert state.passed is False
    assert state.has_controls is False


def test_rotate_result_as_dict_roundtrip() -> None:
    result = RotateResult(
        solved=True,
        final_angle=255.0,
        steps=6,
        attempts=(RotateAttempt(0, 0.0, False), RotateAttempt(6, 255.0, True)),
        elapsed_ms=4200.0,
    )
    d = result.as_dict()
    assert d["solved"] is True
    assert d["final_angle"] == 255.0
    assert d["steps"] == 6
    assert d["attempts"][-1] == {"step": 6, "angle": 255.0, "passed": True}
