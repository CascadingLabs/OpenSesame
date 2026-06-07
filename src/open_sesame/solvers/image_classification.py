"""Hugging Face image classification helpers for image CAPTCHA experiments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ImageClassificationTask = Literal[
    "image-classification",
    "zero-shot-image-classification",
]


@dataclass(frozen=True)
class ImageClassifierConfig:
    model_id: str = "openai/clip-vit-base-patch32"
    task: ImageClassificationTask = "zero-shot-image-classification"
    device: str = "auto"
    cache_dir: Path | None = None
    local_files_only: bool = False
    candidate_labels: tuple[str, ...] = ()


class HuggingFaceImageClassifier:
    """Thin wrapper over HF image classification pipelines."""

    def __init__(self, config: ImageClassifierConfig | None = None) -> None:
        self.config = config or ImageClassifierConfig()
        self._pipeline: Any | None = None

    def classify(self, image_path: str | Path) -> list[dict[str, Any]]:
        try:
            from PIL import Image
        except ImportError as exc:
            msg = "Install the 'ml' extra to run image classification examples."
            raise RuntimeError(msg) from exc

        image = Image.open(image_path).convert("RGB")
        pipe = self.load()
        if self.config.task == "zero-shot-image-classification":
            if not self.config.candidate_labels:
                msg = "zero-shot image classification requires candidate_labels"
                raise ValueError(msg)
            return pipe(image, candidate_labels=list(self.config.candidate_labels))
        return pipe(image)

    def load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        self._prepare_cache_env()
        try:
            from transformers import pipeline
        except ImportError as exc:
            msg = "Install the 'ml' extra to run image classification examples."
            raise RuntimeError(msg) from exc

        _, pipeline_device = resolve_pipeline_device(self.config.device)
        self._pipeline = pipeline(
            task=self.config.task,
            model=self.config.model_id,
            device=pipeline_device,
            cache_dir=str(self.config.cache_dir) if self.config.cache_dir else None,
            local_files_only=self.config.local_files_only,
        )
        return self._pipeline

    def _prepare_cache_env(self) -> None:
        if self.config.cache_dir is None:
            return
        cache_dir = Path(self.config.cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(cache_dir))
        os.environ.setdefault("HF_HUB_CACHE", str(cache_dir / "hub"))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_dir / "transformers"))


def split_image_grid(
    image_path: str | Path,
    *,
    rows: int,
    cols: int,
    output_dir: str | Path,
    prefix: str = "tile",
) -> tuple[Path, ...]:
    """Split an image grid into row-major tile images."""

    if rows < 1 or cols < 1:
        msg = "rows and cols must be positive"
        raise ValueError(msg)
    try:
        from PIL import Image
    except ImportError as exc:
        msg = "Install the 'ml' extra to split image grids."
        raise RuntimeError(msg) from exc

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    image = Image.open(image_path).convert("RGB")
    tile_width = image.width // cols
    tile_height = image.height // rows
    paths: list[Path] = []
    for row in range(rows):
        for col in range(cols):
            left = col * tile_width
            top = row * tile_height
            right = image.width if col == cols - 1 else left + tile_width
            bottom = image.height if row == rows - 1 else top + tile_height
            tile = image.crop((left, top, right, bottom))
            path = output / f"{prefix}-{row}-{col}.png"
            tile.save(path)
            paths.append(path)
    return tuple(paths)


def best_label(results: list[dict[str, Any]]) -> tuple[str, float] | None:
    if not results:
        return None
    best = max(results, key=lambda result: float(result.get("score", 0.0)))
    return str(best.get("label", "")), float(best.get("score", 0.0))


def resolve_pipeline_device(device: str) -> tuple[str, int]:
    if device == "cpu":
        return "cpu", -1
    if device.startswith("cuda"):
        index = 0
        if ":" in device:
            index = int(device.split(":", 1)[1])
        return device, index
    if device == "auto":
        try:
            import torch
        except Exception:
            return "cpu", -1
        if torch.cuda.is_available():
            return "cuda:0", 0
        return "cpu", -1
    msg = "device must be 'auto', 'cpu', or a cuda device like 'cuda:0'"
    raise ValueError(msg)
