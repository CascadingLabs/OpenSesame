#!/usr/bin/env python3
"""Classify one image with a Hugging Face image classification pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from open_sesame.solvers.image_classification import (
    HuggingFaceImageClassifier,
    ImageClassifierConfig,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--model", default="openai/clip-vit-base-patch32")
    parser.add_argument(
        "--task",
        choices=["image-classification", "zero-shot-image-classification"],
        default="zero-shot-image-classification",
    )
    parser.add_argument(
        "--labels",
        help="Comma-separated candidate labels for zero-shot classification.",
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()

    labels = tuple(
        label.strip() for label in (args.labels or "").split(",") if label.strip()
    )
    classifier = HuggingFaceImageClassifier(
        ImageClassifierConfig(
            model_id=args.model,
            task=args.task,
            device=args.device,
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
            candidate_labels=labels,
        )
    )
    results = classifier.classify(args.image)
    for result in results:
        print(f"{result['label']}\t{float(result['score']):.4f}")


if __name__ == "__main__":
    main()
