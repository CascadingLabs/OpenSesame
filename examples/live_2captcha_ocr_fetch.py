#!/usr/bin/env python3
"""Live HTTP-only OCR validation for the 2Captcha normal captcha demo."""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import httpx

from open_sesame.harness.twocaptcha import (
    parse_demo_expected_answer,
    parse_normal_demo_image_url,
)
from open_sesame.solvers.local_ml import LocalMLCaptchaOCRSolver
from open_sesame.solvers.ml_config import LocalOCRConfig, RUNNABLE_MODEL_OPTIONS

TARGET_URL = "https://2captcha.com/demo/normal"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default="grafj-crnn-base",
        choices=sorted(RUNNABLE_MODEL_OPTIONS),
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--allow-remote-code", action="store_true")
    parser.add_argument("--save-image", type=Path)
    args = parser.parse_args()

    page_response = httpx.get(TARGET_URL, timeout=20, follow_redirects=True)
    page_response.raise_for_status()
    expected = parse_demo_expected_answer(page_response.text)
    image_url = parse_normal_demo_image_url(page_response.text, TARGET_URL)
    if expected is None:
        raise RuntimeError("Could not parse expected answer from live demo page")
    if image_url is None:
        raise RuntimeError("Could not parse captcha image URL from live demo page")

    image_response = httpx.get(image_url, timeout=20, follow_redirects=True)
    image_response.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        image_path = Path(tmp.name)
        tmp.write(image_response.content)

    if args.save_image is not None:
        args.save_image.write_bytes(image_response.content)

    solver = LocalMLCaptchaOCRSolver(
        LocalOCRConfig(
            model_id=args.model,
            device=args.device,
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
            allow_remote_code=args.allow_remote_code,
        )
    )
    started = time.perf_counter()
    try:
        result = solver.solve_image(image_path)
        elapsed_ms = (time.perf_counter() - started) * 1000
    finally:
        image_path.unlink(missing_ok=True)

    answer = result.best.text if result.best else ""
    passed = answer == expected
    print(f"target={TARGET_URL}")
    print(f"image_url={image_url}")
    print(f"model={args.model}")
    print(f"device={result.metadata['device']}")
    print(f"expected={expected}")
    print(f"answer={answer}")
    print(f"passed={passed}")
    print(f"elapsed_ms={elapsed_ms:.1f}")
    print(f"confidence={result.best.confidence if result.best else 0.0:.3f}")
    if args.save_image is not None:
        print(f"saved_image={args.save_image}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
