"""Benchmark helpers for local OCR model evaluation."""

from __future__ import annotations

import json
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ResourceSnapshot:
    rss_mb: float
    gpu_util_percent: float | None = None
    gpu_memory_mb: float | None = None


@dataclass(frozen=True)
class BenchmarkSummary:
    load_ms: float
    first_inference_ms: float
    warm_latency_ms_avg: float
    warm_latency_ms_p50: float
    warm_latency_ms_p95: float
    warm_latency_ms_min: float
    warm_latency_ms_max: float
    rss_mb_start: float
    rss_mb_end: float
    rss_mb_peak: float
    cpu_percent_avg: float | None
    gpu_util_percent_peak: float | None
    gpu_memory_mb_peak: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "load_ms": self.load_ms,
            "first_inference_ms": self.first_inference_ms,
            "warm_latency_ms_avg": self.warm_latency_ms_avg,
            "warm_latency_ms_p50": self.warm_latency_ms_p50,
            "warm_latency_ms_p95": self.warm_latency_ms_p95,
            "warm_latency_ms_min": self.warm_latency_ms_min,
            "warm_latency_ms_max": self.warm_latency_ms_max,
            "rss_mb_start": self.rss_mb_start,
            "rss_mb_end": self.rss_mb_end,
            "rss_mb_peak": self.rss_mb_peak,
            "cpu_percent_avg": self.cpu_percent_avg,
            "gpu_util_percent_peak": self.gpu_util_percent_peak,
            "gpu_memory_mb_peak": self.gpu_memory_mb_peak,
        }


def summarize_benchmark(
    *,
    load_ms: float,
    first_inference_ms: float,
    warm_latencies_ms: list[float],
    snapshots: list[ResourceSnapshot],
    cpu_percent_avg: float | None,
) -> BenchmarkSummary:
    if not warm_latencies_ms:
        msg = "warm_latencies_ms must contain at least one latency"
        raise ValueError(msg)
    if not snapshots:
        msg = "snapshots must contain at least one resource snapshot"
        raise ValueError(msg)

    gpu_util_values = [
        snapshot.gpu_util_percent
        for snapshot in snapshots
        if snapshot.gpu_util_percent is not None
    ]
    gpu_memory_values = [
        snapshot.gpu_memory_mb
        for snapshot in snapshots
        if snapshot.gpu_memory_mb is not None
    ]

    return BenchmarkSummary(
        load_ms=load_ms,
        first_inference_ms=first_inference_ms,
        warm_latency_ms_avg=statistics.fmean(warm_latencies_ms),
        warm_latency_ms_p50=statistics.median(warm_latencies_ms),
        warm_latency_ms_p95=percentile(warm_latencies_ms, 95),
        warm_latency_ms_min=min(warm_latencies_ms),
        warm_latency_ms_max=max(warm_latencies_ms),
        rss_mb_start=snapshots[0].rss_mb,
        rss_mb_end=snapshots[-1].rss_mb,
        rss_mb_peak=max(snapshot.rss_mb for snapshot in snapshots),
        cpu_percent_avg=cpu_percent_avg,
        gpu_util_percent_peak=max(gpu_util_values) if gpu_util_values else None,
        gpu_memory_mb_peak=max(gpu_memory_values) if gpu_memory_values else None,
    )


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile_value / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class ResourceSampler:
    """Sample process memory and best-effort GPU stats in the background."""

    def __init__(self, interval: float = 0.02, include_gpu: bool = False) -> None:
        self.interval = interval
        self.include_gpu = include_gpu
        self.samples: list[ResourceSnapshot] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> ResourceSampler:
        self.samples.append(take_resource_snapshot(include_gpu=self.include_gpu))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self.samples.append(take_resource_snapshot(include_gpu=self.include_gpu))

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.samples.append(take_resource_snapshot(include_gpu=self.include_gpu))


def take_resource_snapshot(*, include_gpu: bool = False) -> ResourceSnapshot:
    try:
        import psutil
    except ImportError:
        return ResourceSnapshot(rss_mb=0.0)

    process = psutil.Process()
    rss_mb = process.memory_info().rss / 1024 / 1024
    gpu_util_percent, gpu_memory_mb = read_gpu_stats() if include_gpu else (None, None)
    return ResourceSnapshot(
        rss_mb=rss_mb,
        gpu_util_percent=gpu_util_percent,
        gpu_memory_mb=gpu_memory_mb,
    )


def measure_cpu_percent(
    fn: Callable[[], Any],
) -> tuple[Any, float | None, float]:
    try:
        import psutil
    except ImportError:
        started = time.perf_counter()
        return fn(), None, (time.perf_counter() - started) * 1000

    process = psutil.Process()
    cpu_count = psutil.cpu_count() or 1
    cpu_start = process.cpu_times()
    wall_start = time.perf_counter()
    result = fn()
    wall_elapsed = time.perf_counter() - wall_start
    cpu_end = process.cpu_times()
    cpu_elapsed = (cpu_end.user + cpu_end.system) - (cpu_start.user + cpu_start.system)
    cpu_percent = (cpu_elapsed / wall_elapsed / cpu_count) * 100 if wall_elapsed else 0.0
    return result, cpu_percent, wall_elapsed * 1000


def read_gpu_stats() -> tuple[float | None, float | None]:
    torch_stats = _read_torch_cuda_stats()
    if torch_stats != (None, None):
        return torch_stats
    nvidia_stats = _read_nvidia_smi_stats()
    if nvidia_stats != (None, None):
        return nvidia_stats
    return _read_amd_sysfs_stats()


def _read_torch_cuda_stats() -> tuple[float | None, float | None]:
    try:
        import torch
    except Exception:
        return None, None
    if not torch.cuda.is_available():
        return None, None
    memory_mb = torch.cuda.max_memory_allocated() / 1024 / 1024
    return None, memory_mb


def _read_nvidia_smi_stats() -> tuple[float | None, float | None]:
    command = [
        "nvidia-smi",
        "--query-gpu=utilization.gpu,memory.used",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=0.2,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    first_line = completed.stdout.splitlines()[0] if completed.stdout.splitlines() else ""
    if not first_line:
        return None, None
    try:
        util_text, memory_text = [part.strip() for part in first_line.split(",", 1)]
        return float(util_text), float(memory_text)
    except ValueError:
        return None, None


def _read_amd_sysfs_stats() -> tuple[float | None, float | None]:
    cards = sorted(Path("/sys/class/drm").glob("card*/device"))
    for card in cards:
        util = _read_float(card / "gpu_busy_percent")
        memory_bytes = _read_float(card / "mem_info_vram_used")
        if util is not None or memory_bytes is not None:
            memory_mb = memory_bytes / 1024 / 1024 if memory_bytes is not None else None
            return util, memory_mb
    return None, None


def _read_float(path: Path) -> float | None:
    try:
        return float(path.read_text().strip())
    except (OSError, ValueError):
        return None


def dumps_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True)
