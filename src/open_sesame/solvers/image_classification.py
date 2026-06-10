"""Hugging Face image classification helpers for image CAPTCHA experiments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from open_sesame.solvers.ml_config import resolve_torch_device_info

ImageClassificationTask = Literal[
    "image-classification",
    "zero-shot-image-classification",
]

# CLIP zero-shot accuracy is sensitive to the prompt wrapping the label. The
# original CLIP work averages logits across a bank of prompt templates; on
# noised reCAPTCHA tiles this measurably lifts weak true-positive scores
# without inflating distractors. {} is replaced by each candidate label.
DEFAULT_HYPOTHESIS_TEMPLATES: tuple[str, ...] = (
    "a photo of a {}.",
    "a cropped street photo containing a {}.",
    "a blurry photo of a {}.",
    "a photo of the side of a {}.",
)


@dataclass(frozen=True)
class ImageClassifierConfig:
    model_id: str = "openai/clip-vit-base-patch32"
    task: ImageClassificationTask = "zero-shot-image-classification"
    device: str = "auto"
    cache_dir: Path | None = None
    local_files_only: bool = False
    candidate_labels: tuple[str, ...] = ()
    hypothesis_templates: tuple[str, ...] = ()


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
            labels = list(self.config.candidate_labels)
            templates = self.config.hypothesis_templates
            if not templates:
                return pipe(image, candidate_labels=labels)
            return self._classify_template_ensemble(pipe, image, labels, templates)
        return pipe(image)

    @staticmethod
    def _classify_template_ensemble(
        pipe: Any,
        image: Any,
        labels: list[str],
        templates: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Average per-label scores across prompt templates (CLIP prompt ensemble)."""

        totals: dict[str, float] = {label: 0.0 for label in labels}
        for template in templates:
            for result in pipe(image, candidate_labels=labels, hypothesis_template=template):
                label = str(result.get("label", ""))
                totals[label] = totals.get(label, 0.0) + float(result.get("score", 0.0))
        count = len(templates)
        averaged = [
            {"label": label, "score": total / count}
            for label, total in totals.items()
        ]
        averaged.sort(key=lambda item: item["score"], reverse=True)
        return averaged

    def load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        self._prepare_cache_env()
        try:
            from transformers import pipeline
        except ImportError as exc:
            msg = "Install the 'ml' extra to run image classification examples."
            raise RuntimeError(msg) from exc

        device_info = resolve_torch_device_info(self.config.device)
        # cache_dir / local_files_only belong to model loading, not every
        # pipeline's call signature: the image-classification pipeline rejects
        # a top-level cache_dir that the zero-shot one tolerated. Route them
        # through model_kwargs so both tasks load from the same cache.
        model_kwargs: dict[str, Any] = {}
        if self.config.cache_dir:
            model_kwargs["cache_dir"] = str(self.config.cache_dir)
        if self.config.local_files_only:
            model_kwargs["local_files_only"] = True
        self._pipeline = pipeline(
            task=self.config.task,
            model=self.config.model_id,
            device=device_info.pipeline_device,
            model_kwargs=model_kwargs,
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
    info = resolve_torch_device_info(device)
    return info.torch_device, info.pipeline_device
