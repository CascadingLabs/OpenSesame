#!/usr/bin/env python3
"""Split and classify an image grid for image-group CAPTCHA experiments."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from open_sesame.solvers.image_classification import (
    HuggingFaceImageClassifier,
    ImageClassifierConfig,
    best_label,
    split_image_grid,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--cols", type=int, required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--model", default="openai/clip-vit-base-patch32")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--keep-tiles", type=Path)
    args = parser.parse_args()

    labels = tuple(label.strip() for label in args.labels.split(",") if label.strip())
    if not labels:
        parser.error("--labels must include at least one label")

    classifier = HuggingFaceImageClassifier(
        ImageClassifierConfig(
            model_id=args.model,
            task="zero-shot-image-classification",
            device=args.device,
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
            candidate_labels=labels,
        )
    )

    if args.keep_tiles is not None:
        tile_dir = args.keep_tiles
        tile_paths = split_image_grid(
            args.image,
            rows=args.rows,
            cols=args.cols,
            output_dir=tile_dir,
            prefix=args.image.stem,
        )
        emit_tile_scores(tile_paths, classifier, args.cols)
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tile_paths = split_image_grid(
            args.image,
            rows=args.rows,
            cols=args.cols,
            output_dir=tmpdir,
            prefix=args.image.stem,
        )
        emit_tile_scores(tile_paths, classifier, args.cols)


def emit_tile_scores(
    tile_paths: tuple[Path, ...],
    classifier: HuggingFaceImageClassifier,
    cols: int,
) -> None:
    for index, tile_path in enumerate(tile_paths):
        row, col = divmod(index, cols)
        results = classifier.classify(tile_path)
        best = best_label(results)
        if best is None:
            print(f"{row},{col}\t\t0.0000")
            continue
        label, score = best
        print(f"{row},{col}\t{label}\t{score:.4f}")


if __name__ == "__main__":
    main()
