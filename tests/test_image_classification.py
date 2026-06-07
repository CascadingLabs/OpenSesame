from __future__ import annotations

from pathlib import Path

import pytest

from open_sesame.solvers.image_classification import best_label, split_image_grid


def test_best_label_returns_highest_score() -> None:
    result = best_label(
        [
            {"label": "bus", "score": 0.2},
            {"label": "car", "score": 0.8},
        ]
    )

    assert result == ("car", 0.8)


def test_split_image_grid_writes_row_major_tiles(tmp_path: Path) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    image = pillow.new("RGB", (20, 10), "white")
    image.save(image_path)

    paths = split_image_grid(image_path, rows=2, cols=2, output_dir=tmp_path / "tiles")

    assert [path.name for path in paths] == [
        "tile-0-0.png",
        "tile-0-1.png",
        "tile-1-0.png",
        "tile-1-1.png",
    ]
    assert all(path.exists() for path in paths)


def test_split_image_grid_rejects_invalid_shape(tmp_path: Path) -> None:
    image_path = tmp_path / "grid.png"
    image_path.write_bytes(b"fake")

    with pytest.raises(ValueError, match="positive"):
        split_image_grid(image_path, rows=0, cols=2, output_dir=tmp_path)
