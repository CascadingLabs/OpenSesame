#!/usr/bin/env python3
"""Offline precision/recall eval for reCAPTCHA tile classifiers.

Live challenges give no ground truth (only the minted token would), so tuning
the tile-selection threshold against them is guesswork. This harness scores a
local image-classification model against a *labeled* tile dataset
(``nobodyPerfecZ/recaptchav2-29k``: 100x100 tiles, multi-hot over
``bicycle,bus,car,crosswalk,hydrant``) and reports per-class precision/recall
plus a threshold sweep — so threshold and model changes are measured, not
guessed. Feeds the CAS-181 scoreboard and CAS-193 retraining loop.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

# The dataset's multi-hot label order (matches the companion fine-tuned model).
DATASET_CLASSES = ("bicycle", "bus", "car", "crosswalk", "hydrant")


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    from datasets import load_dataset

    from open_sesame.solvers.image_classification import (
        DEFAULT_HYPOTHESIS_TEMPLATES,
        HuggingFaceImageClassifier,
        ImageClassifierConfig,
    )

    ds = load_dataset(args.dataset, split=args.split, cache_dir=args.cache_dir)
    if args.limit and args.limit < len(ds):
        ds = ds.select(range(args.limit))

    templates = DEFAULT_HYPOTHESIS_TEMPLATES if args.prompt_ensemble else ()
    classifier = HuggingFaceImageClassifier(
        ImageClassifierConfig(
            model_id=args.model,
            task=args.task,
            device=args.device,
            cache_dir=args.cache_dir,
            candidate_labels=DATASET_CLASSES if args.task == "zero-shot-image-classification" else (),
            hypothesis_templates=templates,
        )
    )

    thresholds = [round(t / 100, 2) for t in range(int(args.min_threshold * 100), 100, args.threshold_step)]
    # per (class, threshold): tp, fp, fn
    stats = {cls: {t: [0, 0, 0] for t in thresholds} for cls in DATASET_CLASSES}

    tile_dir = args.cache_dir + "/eval-tiles"
    import os
    os.makedirs(tile_dir, exist_ok=True)

    for index, example in enumerate(ds):
        image = example["image"].convert("RGB")
        truth = {DATASET_CLASSES[i] for i, v in enumerate(example["labels"]) if int(v) == 1}
        tile_path = f"{tile_dir}/tile_{index}.png"
        image.save(tile_path)
        scores = {
            str(r.get("label", "")): float(r.get("score", 0.0))
            for r in classifier.classify(tile_path)
        }
        for cls in DATASET_CLASSES:
            score = scores.get(cls, 0.0)
            is_true = cls in truth
            for t in thresholds:
                predicted = score >= t
                cell = stats[cls][t]
                if predicted and is_true:
                    cell[0] += 1
                elif predicted and not is_true:
                    cell[1] += 1
                elif not predicted and is_true:
                    cell[2] += 1

    return summarize(stats, thresholds, n=len(ds), model=args.model, task=args.task, ensemble=args.prompt_ensemble)


def summarize(stats, thresholds, *, n, model, task, ensemble) -> dict[str, Any]:
    # Best threshold = the one maximizing micro-F1 across all classes.
    micro = {}
    for t in thresholds:
        tp = sum(stats[c][t][0] for c in DATASET_CLASSES)
        fp = sum(stats[c][t][1] for c in DATASET_CLASSES)
        fn = sum(stats[c][t][2] for c in DATASET_CLASSES)
        micro[t] = prf(tp, fp, fn)
    best_t = max(micro, key=lambda t: micro[t]["f1"])

    per_class = {}
    for cls in DATASET_CLASSES:
        tp, fp, fn = stats[cls][best_t]
        per_class[cls] = prf(tp, fp, fn)

    return {
        "model": model,
        "task": task,
        "prompt_ensemble": ensemble,
        "samples": n,
        "best_threshold": best_t,
        "micro_at_best": micro[best_t],
        "per_class_at_best": per_class,
        "threshold_sweep": {str(t): micro[t] for t in thresholds},
    }


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="nobodyPerfecZ/recaptchav2-29k")
    parser.add_argument("--split", default="test")
    parser.add_argument("--model", default="verytuffcat/recaptcha")
    parser.add_argument(
        "--task",
        choices=["image-classification", "zero-shot-image-classification"],
        default="image-classification",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cache-dir", default=".local/hf")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--prompt-ensemble", action="store_true")
    parser.add_argument("--min-threshold", type=float, default=0.1)
    parser.add_argument("--threshold-step", type=int, default=5, help="Threshold step in hundredths.")
    args = parser.parse_args()

    report = evaluate(args)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
