#!/usr/bin/env python3
"""Benchmark local downloadable captcha OCR models."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from open_sesame.harness.benchmarks import (
    ResourceSampler,
    dumps_json,
    measure_cpu_percent,
    summarize_benchmark,
)
from open_sesame.solvers.local_ml import LocalMLCaptchaOCRSolver
from open_sesame.solvers.ml_config import (
    LocalOCRConfig,
    RUNNABLE_MODEL_OPTIONS,
    resolve_torch_device_info,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", help="Captcha image to benchmark.")
    parser.add_argument(
        "--model",
        default="grafj-conv-transformer-base",
        choices=sorted(RUNNABLE_MODEL_OPTIONS),
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument(
        "--local-files-only",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--allow-remote-code", action="store_true")
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.iterations < 1:
        parser.error("--iterations must be >= 1")
    if args.warmups < 0:
        parser.error("--warmups must be >= 0")

    config = LocalOCRConfig(
        model_id=args.model,
        device=args.device,
        cache_dir=args.cache_dir,
        local_files_only=args.local_files_only,
        allow_remote_code=args.allow_remote_code,
    )
    solver = LocalMLCaptchaOCRSolver(config)
    requested_device_info = resolve_torch_device_info(args.device)
    include_gpu = requested_device_info.uses_gpu

    with ResourceSampler(include_gpu=include_gpu) as sampler:
        load_started = time.perf_counter()
        solver.load()
        load_ms = (time.perf_counter() - load_started) * 1000

        first_result, first_cpu_percent, first_ms = measure_cpu_percent(
            lambda: solver.solve_image(args.image)
        )

        for _ in range(args.warmups):
            solver.solve_image(args.image)

        warm_latencies: list[float] = []
        warm_cpu_values: list[float] = []
        last_result = first_result
        for _ in range(args.iterations):
            result, cpu_percent, elapsed_ms = measure_cpu_percent(
                lambda: solver.solve_image(args.image)
            )
            last_result = result
            warm_latencies.append(elapsed_ms)
            if cpu_percent is not None:
                warm_cpu_values.append(cpu_percent)

    cpu_values = warm_cpu_values
    if first_cpu_percent is not None:
        cpu_values = [first_cpu_percent, *warm_cpu_values]
    summary = summarize_benchmark(
        load_ms=load_ms,
        first_inference_ms=first_ms,
        warm_latencies_ms=warm_latencies,
        snapshots=sampler.samples,
        cpu_percent_avg=(sum(cpu_values) / len(cpu_values)) if cpu_values else None,
    )

    output = {
        "model": args.model,
        "repo_id": solver.option.repo_id,
        "device": last_result.metadata["device"],
        "device_info": last_result.metadata.get("device_info", requested_device_info.as_dict()),
        "image": str(args.image),
        "answer": last_result.best.text if last_result.best else "",
        "confidence": last_result.best.confidence if last_result.best else 0.0,
        "warmups": args.warmups,
        "iterations": args.iterations,
        "benchmark": summary.as_dict(),
        "gpu_metrics_available": summary.gpu_util_percent_peak is not None
        or summary.gpu_memory_mb_peak is not None,
    }

    if args.json:
        print(dumps_json(output))
    else:
        print(f"model={output['model']}")
        print(f"repo_id={output['repo_id']}")
        print(f"device={output['device']}")
        device_info = output["device_info"]
        if isinstance(device_info, dict):
            print(f"accelerator={device_info.get('accelerator')}")
            print(f"device_name={device_info.get('device_name')}")
        print(f"answer={output['answer']}")
        print(f"confidence={output['confidence']:.3f}")
        for key, value in output["benchmark"].items():
            if value is None:
                print(f"{key}=null")
            else:
                print(f"{key}={value:.3f}")
        print(f"gpu_metrics_available={output['gpu_metrics_available']}")


if __name__ == "__main__":
    main()
