#!/usr/bin/env python3
"""Throughput probe for the Fortress Cloudflare managed challenge target."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass

import httpx

from open_sesame.harness.antibot import classify_antibot_response

TARGET_URL = "https://fortress.theplumber.dev/"


@dataclass(frozen=True)
class ProbeResult:
    index: int
    ok: bool
    status_code: int | None
    elapsed_ms: float
    vendor: str | None
    challenge_type: str | None
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "ok": self.ok,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "vendor": self.vendor,
            "challenge_type": self.challenge_type,
            "error": self.error,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=TARGET_URL)
    parser.add_argument("--attempts", type=int, default=25)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.attempts < 1:
        parser.error("--attempts must be >= 1")
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")

    summary, results = asyncio.run(
        run_probe(
            args.url,
            attempts=args.attempts,
            concurrency=args.concurrency,
            timeout=args.timeout,
        )
    )
    if args.json:
        print(json.dumps({"summary": summary, "results": [r.as_dict() for r in results]}, indent=2))
        return

    print(f"url={summary['url']}")
    print(f"attempts={summary['attempts']}")
    print(f"concurrency={summary['concurrency']}")
    print(f"ok={summary['ok']}")
    print(f"challenges={summary['challenges']}")
    print(f"errors={summary['errors']}")
    print(f"throughput_rps={summary['throughput_rps']:.2f}")
    print(f"latency_ms_avg={summary['latency_ms_avg']:.1f}")
    print(f"latency_ms_p95={summary['latency_ms_p95']:.1f}")
    print(f"vendors={summary['vendors']}")


async def run_probe(
    url: str,
    *,
    attempts: int,
    concurrency: int,
    timeout: float,
) -> tuple[dict[str, object], tuple[ProbeResult, ...]]:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    semaphore = asyncio.Semaphore(concurrency)
    started = time.perf_counter()
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"user-agent": "OpenSesame throughput probe"},
    ) as client:
        results = await asyncio.gather(
            *(probe_once(client, semaphore, url, index, timeout) for index in range(attempts))
        )
    elapsed = time.perf_counter() - started
    return summarize(url, attempts, concurrency, elapsed, results), tuple(results)


async def probe_once(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
    index: int,
    timeout: float,
) -> ProbeResult:
    async with semaphore:
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(client.get(url), timeout=timeout)
            elapsed_ms = (time.perf_counter() - started) * 1000
            verdict = classify_antibot_response(
                response.text,
                status_code=response.status_code,
                headers=response.headers,
            )
            return ProbeResult(
                index=index,
                ok=True,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
                vendor=verdict.vendor,
                challenge_type=verdict.challenge_type,
            )
        except Exception as exc:  # pragma: no cover - live network path
            elapsed_ms = (time.perf_counter() - started) * 1000
            return ProbeResult(
                index=index,
                ok=False,
                status_code=None,
                elapsed_ms=elapsed_ms,
                vendor=None,
                challenge_type=None,
                error=f"{type(exc).__name__}: {exc}",
            )


def summarize(
    url: str,
    attempts: int,
    concurrency: int,
    elapsed: float,
    results: list[ProbeResult],
) -> dict[str, object]:
    ok_results = [result for result in results if result.ok]
    challenged = [result for result in ok_results if result.vendor]
    latencies = [result.elapsed_ms for result in results]
    vendors = sorted({result.vendor for result in challenged if result.vendor})

    return {
        "url": url,
        "attempts": attempts,
        "concurrency": concurrency,
        "ok": len(ok_results),
        "challenges": len(challenged),
        "errors": attempts - len(ok_results),
        "throughput_rps": attempts / elapsed if elapsed > 0 else 0.0,
        "latency_ms_avg": statistics.fmean(latencies) if latencies else 0.0,
        "latency_ms_p95": percentile(latencies, 95),
        "vendors": vendors,
    }


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile_value / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


if __name__ == "__main__":
    main()
