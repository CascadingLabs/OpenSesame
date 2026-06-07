#!/usr/bin/env python3
"""Evaluate OCR solvers against a labeled image corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from open_sesame.harness.eval import evaluate_corpus, load_jsonl_corpus
from open_sesame.solvers.local_ml import LocalMLCaptchaOCRSolver
from open_sesame.solvers.ml_config import LocalOCRConfig, RUNNABLE_MODEL_OPTIONS
from open_sesame.solvers.ocr import TesseractOCRSolver


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path, help="JSONL corpus manifest.")
    parser.add_argument("--solver", choices=["tesseract", "local-ml"], default="local-ml")
    parser.add_argument(
        "--model",
        default="grafj-crnn-base",
        choices=sorted(RUNNABLE_MODEL_OPTIONS),
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--allow-remote-code", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    samples = load_jsonl_corpus(args.corpus)
    solver_name, solve_image = build_solver(args)
    summary, results = evaluate_corpus(samples, solver_name, solve_image)

    payload = {
        "solver": solver_name,
        "summary": summary.as_dict(),
        "results": [result.as_dict() for result in results],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print(f"solver={solver_name}")
    for key, value in summary.as_dict().items():
        print(f"{key}={value}")
    for result in results:
        status = "PASS" if result.exact else "FAIL"
        print(
            f"{status} {result.id} expected={result.expected} "
            f"answer={result.answer} cer={result.cer:.3f} "
            f"elapsed_ms={result.elapsed_ms:.1f}"
        )


def build_solver(args: argparse.Namespace):
    if args.solver == "tesseract":
        solver = TesseractOCRSolver()
        return "tesseract", solver.solve_image

    solver = LocalMLCaptchaOCRSolver(
        LocalOCRConfig(
            model_id=args.model,
            device=args.device,
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
            allow_remote_code=args.allow_remote_code,
        )
    )
    solver.load()
    return args.model, solver.solve_image


if __name__ == "__main__":
    main()
