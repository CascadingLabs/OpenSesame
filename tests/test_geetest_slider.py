"""Unit tests for GeeTest slider pure helpers (no live browser)."""

from __future__ import annotations

from open_sesame.harness.geetest_slider import (
    GeetestRect,
    human_drag_path,
    parse_gap,
)


def test_parse_gap_reads_geometry_and_button() -> None:
    raw = {
        "ok": True,
        "gapLeft": 180,
        "gapRight": 220,
        "sliceLeft": 8,
        "canvasW": 260,
        "dispW": 258.0,
        "scale": 258.0 / 260.0,
        "slider_button": {"x": 100.0, "y": 400.0, "width": 40.0, "height": 40.0},
    }
    gap = parse_gap(raw)
    assert gap.ok is True
    assert gap.gap_left == 180
    assert gap.slice_left == 8
    assert gap.slider_button == GeetestRect(100.0, 400.0, 40.0, 40.0)
    # (180 - 8) * (258/260) ≈ 170.7 CSS px
    assert abs(gap.drag_distance_css - (172 * 258.0 / 260.0)) < 0.01


def test_parse_gap_failure_carries_reason() -> None:
    gap = parse_gap({"ok": False, "reason": "no-canvas"})
    assert gap.ok is False
    assert gap.reason == "no-canvas"
    assert gap.drag_distance_css == 0.0


def test_parse_gap_handles_garbage() -> None:
    assert parse_gap(None).ok is False
    assert parse_gap("nope").ok is False


def test_human_drag_path_starts_at_origin_and_lands_on_target() -> None:
    start_x, start_y, distance = 120.0, 410.0, 168.0
    path = human_drag_path(start_x, start_y, distance, steps=30, seed=5)
    # First point starts at the press location.
    assert abs(path[0][0] - start_x) < 0.001
    assert abs(path[0][1] - start_y) < 0.001
    # Final point settles at the target offset (within sub-pixel tolerance).
    assert abs(path[-1][0] - (start_x + distance)) < 0.5
    # The path overshoots past the target before settling back.
    assert max(x for x, _ in path) > start_x + distance


def test_human_drag_path_is_deterministic_per_seed() -> None:
    a = human_drag_path(0.0, 0.0, 100.0, seed=11)
    b = human_drag_path(0.0, 0.0, 100.0, seed=11)
    assert a == b
