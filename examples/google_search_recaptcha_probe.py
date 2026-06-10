#!/usr/bin/env python3
"""Run headless Google Search queries through VoidCrawl and record reCAPTCHA walls."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from open_sesame.harness.google_search import run_google_search_probe


DEFAULT_QUERIES = (
    "site:example.com captcha test",
    "open source web crawling captcha handling",
    "weather new york",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("queries", nargs="*", default=list(DEFAULT_QUERIES))
    parser.add_argument("--headful", action="store_true", help="Run visible Chrome instead of headless.")
    parser.add_argument("--chrome-executable")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument("--hl", default="en")
    parser.add_argument("--gl", default="us")
    parser.add_argument("--screenshot-dir", type=Path, default=Path(".local/google-search-shots"))
    parser.add_argument("--json", action="store_true", help="Print one JSON array instead of JSONL.")
    args = parser.parse_args()

    results = asyncio.run(
        run_google_search_probe(
            args.queries,
            headless=not args.headful,
            chrome_executable=args.chrome_executable,
            timeout=args.timeout,
            pause=args.pause,
            screenshot_dir=args.screenshot_dir,
            hl=args.hl,
            gl=args.gl,
        )
    )

    records = [result.as_dict() for result in results]
    if args.json:
        print(json.dumps(records, indent=2))
        return

    for record in records:
        print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
