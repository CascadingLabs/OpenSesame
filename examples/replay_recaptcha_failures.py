#!/usr/bin/env python3
"""Export and replay failed local reCAPTCHA attempts against tile models."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from open_sesame.harness.recaptcha_v2 import (
    export_recaptcha_failure_corpus,
    infer_tile_grid_shape_with_python,
    inspect_tile_visual_states_with_python,
    label_variants,
    plan_tile_clicks_ensemble_with_python,
)


def replay_example(example: object, args: argparse.Namespace) -> dict[str, object]:
    record = example.as_dict()
    challenge_image = record.get("challenge_image_path")
    target_label = record.get("target_label")
    if not challenge_image or not target_label:
        record["replay"] = {
            "ok": False,
            "error": "missing challenge_image_path or target_label",
        }
        return record

    labels = tuple(label.strip() for label in args.labels.split(",") if label.strip())
    for variant in label_variants(str(target_label)):
        if variant not in labels:
            labels = (*labels, variant)
    model_ids = tuple(model.strip() for model in args.models.split(",") if model.strip())
    rows, cols = (args.rows, args.cols)
    if rows < 1 or cols < 1:
        rows, cols = infer_tile_grid_shape_with_python(
            str(challenge_image),
            ml_python=args.ml_python,
        )
    tile_states = inspect_tile_visual_states_with_python(
        str(challenge_image),
        rows=rows,
        cols=cols,
        ml_python=args.ml_python,
    )
    active_tiles = tuple((state.row, state.col) for state in tile_states if state.active)
    decisions = plan_tile_clicks_ensemble_with_python(
        str(challenge_image),
        target_label=str(target_label),
        candidate_labels=labels,
        model_ids=model_ids,
        rows=rows,
        cols=cols,
        min_consensus=args.min_consensus,
        min_target_score=args.min_target_score,
        min_score_margin=args.min_score_margin,
        device=args.device,
        cache_dir=args.cache_dir,
        local_files_only=args.local_files_only,
        ml_python=args.ml_python,
        active_tiles=active_tiles,
        augmentation_preset=args.augmentations,
    )
    record["replay"] = {
        "ok": True,
        "rows": rows,
        "cols": cols,
        "device": args.device,
        "augmentations": args.augmentations,
        "active_tiles": [state.as_dict() for state in tile_states],
        "ensemble_plan": [decision.as_dict() for decision in decisions],
    }
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", type=Path, default=Path(".local/recaptcha-runs"))
    parser.add_argument("--corpus-dir", type=Path, default=Path(".local/recaptcha-failures"))
    parser.add_argument("--no-replay", action="store_true", help="Only export the failure corpus.")
    parser.add_argument("--example-id", help="Replay/export only a specific failure example id.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum examples to print/replay; 0 means all.")
    parser.add_argument("--labels", default="bus,crosswalk,traffic light,car,bicycle,motorcycle,stairs,chimney")
    parser.add_argument("--rows", type=int, default=0, help="Grid rows; 0 infers from the saved crop.")
    parser.add_argument("--cols", type=int, default=0, help="Grid columns; 0 infers from the saved crop.")
    parser.add_argument("--models", default="openai/clip-vit-base-patch32")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--min-consensus", type=float, default=1.0)
    parser.add_argument("--min-target-score", type=float, default=0.40)
    parser.add_argument("--min-score-margin", type=float, default=0.10)
    parser.add_argument("--augmentations", choices=["none", "helpful"], default="none")
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--ml-python", default="python")
    args = parser.parse_args()

    examples = export_recaptcha_failure_corpus(
        audit_dir=args.audit_dir,
        corpus_dir=args.corpus_dir,
    )
    if args.example_id:
        examples = tuple(example for example in examples if example.example_id == args.example_id)
    if args.limit > 0:
        examples = examples[: args.limit]
    if args.no_replay:
        records = [example.as_dict() for example in examples]
    else:
        records = [replay_example(example, args) for example in examples]
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
