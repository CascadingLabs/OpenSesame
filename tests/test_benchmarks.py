from __future__ import annotations

from open_sesame.harness.benchmarks import (
    ResourceSnapshot,
    percentile,
    summarize_benchmark,
)


def test_percentile_interpolates_values() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 3.8499999999999996


def test_summarize_benchmark_reports_latency_and_resource_peaks() -> None:
    summary = summarize_benchmark(
        load_ms=100.0,
        first_inference_ms=25.0,
        warm_latencies_ms=[5.0, 7.0, 6.0],
        snapshots=[
            ResourceSnapshot(rss_mb=100.0, gpu_util_percent=None, gpu_memory_mb=None),
            ResourceSnapshot(rss_mb=150.0, gpu_util_percent=20.0, gpu_memory_mb=512.0),
            ResourceSnapshot(rss_mb=125.0, gpu_util_percent=10.0, gpu_memory_mb=256.0),
        ],
        cpu_percent_avg=12.5,
    )

    assert summary.load_ms == 100.0
    assert summary.first_inference_ms == 25.0
    assert summary.warm_latency_ms_avg == 6.0
    assert summary.warm_latency_ms_p50 == 6.0
    assert summary.rss_mb_start == 100.0
    assert summary.rss_mb_end == 125.0
    assert summary.rss_mb_peak == 150.0
    assert summary.cpu_percent_avg == 12.5
    assert summary.gpu_util_percent_peak == 20.0
    assert summary.gpu_memory_mb_peak == 512.0
