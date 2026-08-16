#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "yosoi==0.0.3a21",
#   "voidcrawl==0.3.8.2",
# ]
# ///
"""Runnable Yosoi + VoidCrawl -> OpenSesame interrupt example.

Latest PyPI versions checked with Yosoi on 2026-07-05:
- yosoi==0.0.3a21
- voidcrawl==0.3.8.2

Run from this OpenSesame checkout:

    # terminal 1
    uv run opensesame serve --no-notify --no-open-on-event --no-open-prompt

    # terminal 2: installs the pinned latest published Yosoi/VoidCrawl into an
    # isolated uv script env, imports both, and posts an interrupt to OpenSesame.
    uv run --script examples/yosoi_voidcrawl_latest_interrupt.py --demo-interrupt

Optional: let Yosoi acquire a real URL first. If Yosoi returns a blocked
interrupt, this script forwards it; otherwise it posts a demo interrupt with
Yosoi's fetch metadata as evidence.

    uv run --script examples/yosoi_voidcrawl_latest_interrupt.py \
      --url https://example.com --fetcher simple

Optional: use a live VoidCrawl browser capture. This requires a working local
browser/VoidCrawl setup and may open/navigate Chrome:

    uv run --script examples/yosoi_voidcrawl_latest_interrupt.py \
      --voidcrawl-url https://2captcha.com/demo/cloudflare-turnstile
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib import error, request
from uuid import uuid4

LATEST_YOSOI = "0.0.3a21"
LATEST_VOIDCRAWL = "0.3.8.2"


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=15) as response:  # noqa: S310 - user URL
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {url} failed: HTTP {exc.code}: {detail}") from exc


def demo_interrupt(*, yosoi_version: str, voidcrawl_version: str) -> dict[str, Any]:
    event_id = f"latest-demo-{uuid4()}"
    return {
        "version": "interrupt.v1",
        "status": "blocked",
        "interrupt": {
            "id": event_id,
            "source": "opensesame.examples.yosoi_voidcrawl_latest_interrupt",
            "kind": "bot_wall",
            "subkind": "demo",
            "blocking": True,
            "url": "https://example.test/demo-blocked",
            "attach": {"session_id": "latest-example"},
            "evidence": {
                "source": "latest-import-smoke",
                "data": {
                    "yosoi_version": yosoi_version,
                    "voidcrawl_version": voidcrawl_version,
                    "expected_yosoi_version": LATEST_YOSOI,
                    "expected_voidcrawl_version": LATEST_VOIDCRAWL,
                },
            },
            "resume_hint": {"strategy": "rerun_after_resolution"},
        },
    }


def envelope_from_interrupt(interrupt: dict[str, Any]) -> dict[str, Any]:
    payload = dict(interrupt)
    payload.setdefault("id", f"yosoi-{uuid4()}")
    payload.setdefault("source", "yosoi.fetch")
    payload.setdefault("kind", "bot_wall")
    payload.setdefault("blocking", True)
    if not payload.get("attach"):
        payload["attach"] = {"session_id": "yosoi-fetch"}
    return {"version": "interrupt.v1", "status": "blocked", "interrupt": payload}


async def yosoi_interrupt_for_url(url: str, *, fetcher: str) -> dict[str, Any]:
    import yosoi as ys

    result = await ys.fetch(url, view="metadata", fetcher_type=fetcher)
    if getattr(result, "interrupts", None):
        return envelope_from_interrupt(result.interrupts[0])

    unit = result.results[0]
    event_id = f"yosoi-ok-{uuid4()}"
    return {
        "version": "interrupt.v1",
        "status": "blocked",
        "interrupt": {
            "id": event_id,
            "source": "yosoi.fetch",
            "kind": "bot_wall",
            "subkind": "demo_not_blocked",
            "blocking": True,
            "url": url,
            "attach": {"session_id": "yosoi-fetch"},
            "evidence": {
                "source": "yosoi.fetch",
                "data": {
                    "note": (
                        "Yosoi did not return blocked; demo event queued "
                        "intentionally."
                    ),
                    "fetch_status": result.status,
                    "unit_status": unit.status,
                    "status_code": unit.status_code,
                    "title": unit.title,
                    "fetcher_type": unit.fetcher_type,
                },
            },
            "resume_hint": {"strategy": "none_demo_only"},
        },
    }


async def voidcrawl_capture(url: str) -> dict[str, Any]:
    from voidcrawl import BrowserConfig, BrowserSession

    async with BrowserSession(BrowserConfig(headless=False)) as browser:
        page = await browser.new_page("about:blank")
        try:
            await page.goto(url, timeout=30.0)
        except Exception as exc:  # live challenge pages often do not settle cleanly
            print(f"navigation did not fully settle: {exc}", file=sys.stderr)
        capture = await page.capture_challenge(session_id="voidcrawl-latest-example")
        if "interrupt" in capture:
            interrupt = dict(capture["interrupt"])
            challenge = capture.get("challenge") or {}
            if not interrupt.get("id") and challenge.get("event_id"):
                interrupt["id"] = str(challenge["event_id"])
            return {
                "version": "interrupt.v1",
                "status": "blocked",
                "interrupt": interrupt,
            }
        return capture


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opensesame-url", default="http://127.0.0.1:8765")
    parser.add_argument("--demo-interrupt", action="store_true")
    parser.add_argument("--url", help="URL to acquire with Yosoi before posting")
    parser.add_argument("--fetcher", default="simple", help="Yosoi fetcher type")
    parser.add_argument("--voidcrawl-url", help="URL to capture with VoidCrawl")
    args = parser.parse_args()

    import voidcrawl  # noqa: F401 - import smoke for the latest pinned dependency
    import yosoi  # noqa: F401 - import smoke for the latest pinned dependency

    yosoi_version = package_version("yosoi")
    voidcrawl_version = package_version("voidcrawl")
    print(f"imported yosoi=={yosoi_version} (latest checked: {LATEST_YOSOI})")
    print(
        f"imported voidcrawl=={voidcrawl_version} "
        f"(latest checked: {LATEST_VOIDCRAWL})"
    )

    if args.voidcrawl_url:
        payload = await voidcrawl_capture(args.voidcrawl_url)
        endpoint = (
            "/api/voidcrawl/challenge" if "challenge" in payload else "/api/interrupts"
        )
    elif args.url:
        payload = await yosoi_interrupt_for_url(args.url, fetcher=args.fetcher)
        endpoint = "/api/interrupts"
    else:
        if not args.demo_interrupt:
            print("No --url/--voidcrawl-url supplied; posting --demo-interrupt.")
        payload = demo_interrupt(
            yosoi_version=yosoi_version,
            voidcrawl_version=voidcrawl_version,
        )
        endpoint = "/api/interrupts"

    target = f"{args.opensesame_url.rstrip('/')}{endpoint}"
    response = await asyncio.to_thread(post_json, target, payload)
    event = response.get("event", {})
    print(json.dumps(response, indent=2))
    if event.get("event_id"):
        print(f"OpenSesame UI: {args.opensesame_url}/#event-{event['event_id']}")


if __name__ == "__main__":
    asyncio.run(main())
