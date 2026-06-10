from __future__ import annotations

from pathlib import Path

import pytest

from open_sesame.solvers.image_classification import (
    HuggingFaceImageClassifier,
    ImageClassifierConfig,
    best_label,
    split_image_grid,
)


class _FakePipe:
    """Stub HF zero-shot pipeline that scores per (label, template)."""

    def __init__(self, table: dict[tuple[str, str], float]) -> None:
        self.table = table
        self.calls: list[str] = []

    def __call__(self, _image, candidate_labels, hypothesis_template=None):
        self.calls.append(hypothesis_template or "")
        return [
            {"label": label, "score": self.table[(label, hypothesis_template or "")]}
            for label in candidate_labels
        ]


def test_classify_template_ensemble_averages_scores(tmp_path, monkeypatch) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "tile.png"
    pillow.new("RGB", (8, 8), "blue").save(image_path)

    templates = ("a photo of a {}.", "a blurry photo of a {}.")
    table = {
        ("bus", templates[0]): 0.2,
        ("bus", templates[1]): 0.8,
        ("road", templates[0]): 0.8,
        ("road", templates[1]): 0.2,
    }
    fake = _FakePipe(table)

    classifier = HuggingFaceImageClassifier(
        ImageClassifierConfig(
            candidate_labels=("bus", "road"),
            hypothesis_templates=templates,
        )
    )
    monkeypatch.setattr(classifier, "load", lambda: fake)

    results = classifier.classify(image_path)

    # Each label averaged across both templates -> 0.5 / 0.5, sorted desc.
    scores = {item["label"]: item["score"] for item in results}
    assert scores["bus"] == pytest.approx(0.5)
    assert scores["road"] == pytest.approx(0.5)
    assert fake.calls == list(templates)


def test_classify_without_templates_calls_pipeline_once(tmp_path, monkeypatch) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "tile.png"
    pillow.new("RGB", (8, 8), "blue").save(image_path)

    class OnePassPipe:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _image, candidate_labels, hypothesis_template=None):
            self.calls += 1
            assert hypothesis_template is None
            return [{"label": candidate_labels[0], "score": 0.9}]

    pipe = OnePassPipe()
    classifier = HuggingFaceImageClassifier(
        ImageClassifierConfig(candidate_labels=("bus", "road"))
    )
    monkeypatch.setattr(classifier, "load", lambda: pipe)

    classifier.classify(image_path)

    assert pipe.calls == 1


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
