#!/usr/bin/env python3
"""Run an explicit CAS-253 VoidCrawl interrupt through the OpenSesame UI.

This is an operator-handoff test, not a detector or solver. The caller declares
its interrupt type, retains the same VoidCrawl page, and only resumes after the
operator presses **Resolved** in OpenSesame.

Run from the OpenSesame checkout:

    # terminal 1: a browser appliance with a visible remote browser
    ../VoidCrawl/docker/run-browser.sh

    # terminal 2: the HTMX operator UI
    uv run opensesame watch

    # terminal 3: run against the CAS-253 workspace
    PYTHONPATH=../cas-253-voidcrawl-interrupt--VoidCrawl \
      uv run --no-sync python examples/cas253_interrupt_handoff.py

The default viewer is VoidCrawl's local noVNC service at
``http://127.0.0.1:6080``. Override it with ``--novnc-url URL`` when needed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from urllib import error, parse, request
from uuid import uuid4

from opensesame.voidcrawl_interrupts import (
    VoidCrawlHandoffConfig,
    VoidCrawlInterruptHandoff,
)

DEFAULT_CDP_VERSION_URL = "http://127.0.0.1:19222/json/version"
DEFAULT_NOVNC_URL = "http://127.0.0.1:6080"
DEFAULT_URL = "https://example.com"


def json_request(url: str) -> dict[str, object]:
    try:
        with request.urlopen(url, timeout=15) as response:  # noqa: S310 - local/user URL
            payload = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected an object from {url}")
    return payload


def resolve_browser_ws_url(version_url: str) -> str:
    payload = json_request(version_url)
    raw_ws_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(raw_ws_url, str) or not raw_ws_url:
        raise RuntimeError(f"No webSocketDebuggerUrl in {version_url}: {payload!r}")
    version = parse.urlparse(version_url)
    websocket = parse.urlparse(raw_ws_url)
    scheme = "wss" if version.scheme == "https" else "ws"
    return parse.urlunparse(
        (scheme, version.netloc, websocket.path, "", websocket.query, "")
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Page to park for review")
    parser.add_argument(
        "--kind",
        choices=("captcha", "manual", "sensitive_action", "authentication"),
        default="manual",
        help="Caller-declared interrupt type; OpenSesame displays this label.",
    )
    parser.add_argument("--subkind", default="operator_review")
    parser.add_argument("--code", default=None)
    parser.add_argument("--summary", default=None)
    parser.add_argument("--opensesame-url", default="http://127.0.0.1:8765")
    parser.add_argument("--docker-version-url", default=DEFAULT_CDP_VERSION_URL)
    parser.add_argument("--handoff-url", default=None)
    parser.add_argument("--novnc-url", default=DEFAULT_NOVNC_URL)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--timeout", type=int, default=600)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    from voidcrawl import BrowserConfig, BrowserSession, InterruptRequest

    handoff_url = args.handoff_url or None
    novnc_url = args.novnc_url or None
    config = VoidCrawlHandoffConfig(
        opensesame_url=args.opensesame_url,
        session_id=args.session_id or f"cas253-ui-test-{uuid4()}",
        handoff_url=handoff_url,
        novnc_url=novnc_url,
        resolution_timeout_seconds=args.timeout,
    )
    code = args.code or f"{args.kind}.human_required"
    summary = args.summary or f"Human {args.kind.replace('_', ' ')} review required."
    ws_url = resolve_browser_ws_url(args.docker_version_url)

    async with BrowserSession(BrowserConfig(ws_url=ws_url, headless=False)) as browser:
        page = await browser.new_page(args.url)
        handoff = VoidCrawlInterruptHandoff(config)
        print(
            "Open the OpenSesame UI and complete the step in the configured viewer; "
            "then press Resolved."
        )
        print(f"UI: {args.opensesame_url}/#events")
        result = await handoff.interrupt_and_wait(
            browser,
            page,
            InterruptRequest(code=code, summary=summary, ttl_seconds=int(args.timeout)),
            kind=args.kind,
            subkind=args.subkind,
        )
        print(json.dumps(result.model_dump(), indent=2))
        print(f"Same retained page URL: {await page.url()}")


if __name__ == "__main__":
    asyncio.run(main())
