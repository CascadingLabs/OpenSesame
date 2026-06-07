#!/usr/bin/env python3
"""List, download, and run local downloadable captcha OCR models."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from open_sesame.solvers.local_ml import LocalMLCaptchaOCRSolver
from open_sesame.solvers.ml_config import (
    LocalOCRConfig,
    MODEL_OPTIONS,
    RUNNABLE_MODEL_OPTIONS,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?", help="Captcha image to solve.")
    parser.add_argument(
        "--model",
        default="grafj-conv-transformer-base",
        choices=sorted(RUNNABLE_MODEL_OPTIONS),
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--allow-remote-code", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for option in MODEL_OPTIONS.values():
            marker = " (recommended)" if option.recommended else ""
            status = "runnable" if option.runnable else "research-only"
            print(f"{option.id}{marker}")
            print(f"  repo={option.repo_id}")
            print(f"  revision={option.revision or 'default'}")
            print(f"  backend={option.backend}")
            print(f"  status={status}")
            print(f"  license={option.license}")
            print(f"  trust_remote_code={option.trust_remote_code}")
            print(f"  notes={option.notes}")
        return

    config = LocalOCRConfig(
        model_id=args.model,
        device=args.device,
        cache_dir=args.cache_dir,
        local_files_only=args.local_files_only,
        allow_remote_code=args.allow_remote_code,
    )
    solver = LocalMLCaptchaOCRSolver(config)

    if args.download:
        path = solver.download()
        print(f"downloaded={path}")

    if args.image:
        started = time.perf_counter()
        result = solver.solve_image(args.image)
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"model={args.model}")
        print(f"device={result.metadata['device']}")
        print(f"elapsed_ms={elapsed_ms:.1f}")
        print(f"raw={result.metadata['raw_prediction']}")
        print(f"answer={result.best.text if result.best else ''}")
        print(f"confidence={result.best.confidence if result.best else 0.0:.3f}")

    if not args.download and not args.image:
        parser.error("provide --list, --download, or an image path")


if __name__ == "__main__":
    main()
