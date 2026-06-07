#!/usr/bin/env python3
"""Explore Fortress as an OpenSesame/Yosoi/VoidCrawl gauntlet target."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from open_sesame.harness.gauntlet import (
    crawl_gauntlet_httpx,
    crawl_gauntlet_voidcrawl_profile,
    probe_yosoi_auto,
)

TARGET_URL = "https://fortress.theplumber.dev/"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=TARGET_URL)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--engine",
        choices=["httpx", "yosoi-auto", "voidcrawl-profile"],
        default="httpx",
    )
    parser.add_argument("--yosoi-path", type=Path, default=Path("../Yosoi"))
    parser.add_argument("--profile-dir", type=Path, default=Path(".local/fortress-profile"))
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--chrome-executable")
    parser.add_argument("--screenshot-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.engine == "yosoi-auto":
        summary = None
        results = (asyncio.run(probe_yosoi_auto(args.url, yosoi_path=args.yosoi_path, timeout=int(args.timeout))),)
    elif args.engine == "voidcrawl-profile":
        summary = None
        results = ()
        for _ in range(args.attempts):
            summary, results = asyncio.run(
                crawl_gauntlet_voidcrawl_profile(
                    args.url,
                    profile_dir=args.profile_dir,
                    max_pages=args.max_pages,
                    timeout=args.timeout,
                    headful=not args.headless,
                    chrome_executable=args.chrome_executable,
                    screenshot_dir=args.screenshot_dir,
                )
            )
            if not results or not results[0].blocked:
                break
    else:
        summary, results = asyncio.run(
            crawl_gauntlet_httpx(args.url, max_pages=args.max_pages, timeout=args.timeout)
        )

    payload = {
        "summary": summary.as_dict() if summary is not None else {
            "start_url": args.url,
            "engine": "yosoi-auto",
            "visited": len(results),
            "blocked": sum(1 for result in results if result.blocked),
            "errors": sum(1 for result in results if not result.ok),
            "discovered": len(results),
            "elapsed_ms": sum(result.elapsed_ms for result in results),
        },
        "results": [result.as_dict() for result in results],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return

    summary_data = payload["summary"]
    print(f"url={summary_data['start_url']}")
    print(f"engine={summary_data['engine']}")
    print(f"visited={summary_data['visited']}")
    print(f"blocked={summary_data['blocked']}")
    print(f"errors={summary_data['errors']}")
    for index, result in enumerate(results, start=1):
        vendor = result.verdict.vendor or "none"
        challenge = result.verdict.challenge_type or "none"
        print(
            f"{index}. status={result.status_code} blocked={result.blocked} "
            f"vendor={vendor} challenge={challenge} title={result.title!r} url={result.url}"
        )


if __name__ == "__main__":
    main()
