#!/usr/bin/env python3
"""Run the local captcha+login fixture through the OpenSesame operator queue.

This is the CAS-261 dogfooding loop, end to end and fully local:

    fixture login page -> VoidCrawl parks the tab -> OpenSesame queues it with a
    noVNC viewer -> a human solves it -> the same tab resumes -> the caller
    verifies it reached /secure.

OpenSesame is bound as the session's interrupt handler when the session opens,
so the handoff is a declared part of this run rather than an error path.

Run from the OpenSesame checkout while VoidCrawl's headful container is up:

    # terminal 1 -- the browser appliance, from the VoidCrawl checkout
    ./docker/run-headful.sh -d

    # terminal 2 -- the operator UI
    uv run --no-sync opensesame watch

    # terminal 3 -- this demo
    PYTHONPATH=../VoidCrawl \
      uv run --no-sync python examples/captcha_login_handoff.py

Fixture credentials (local and disposable):

    username: operator
    password: open-sesame

The challenge code is the skewed text on the page; read it over noVNC and type
it in. Then click **Resolved** in the OpenSesame queue.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from urllib import parse, request
from uuid import uuid4

from opensesame.fixtures import (
    FIXTURE_PASSWORD,
    FIXTURE_USERNAME,
    captcha_login_server,
)
from opensesame.voidcrawl_interrupts import (
    VoidCrawlHandoffConfig,
    bind_opensesame,
)

DEFAULT_CDP_VERSION_URL = "http://127.0.0.1:19222/json/version"
DEFAULT_NOVNC_URL = "http://127.0.0.1:6080"


def resolve_browser_ws_url(version_url: str) -> str:
    with request.urlopen(version_url, timeout=15) as response:  # noqa: S310 - local URL
        payload = json.loads(response.read().decode("utf-8"))
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
    parser.add_argument("--opensesame-url", default="http://127.0.0.1:8765")
    parser.add_argument("--docker-version-url", default=DEFAULT_CDP_VERSION_URL)
    parser.add_argument("--novnc-url", default=DEFAULT_NOVNC_URL)
    parser.add_argument("--timeout", type=int, default=600)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    from voidcrawl import BrowserConfig, BrowserSession

    ws_url = resolve_browser_ws_url(args.docker_version_url)
    handoff_config = VoidCrawlHandoffConfig(
        opensesame_url=args.opensesame_url,
        session_id=f"captcha-login-{uuid4()}",
        novnc_url=args.novnc_url,
        resolution_timeout_seconds=args.timeout,
    )

    with captcha_login_server() as (base_url, _fixture):
        browser_config = BrowserConfig(ws_url=ws_url, headless=False)
        async with (
            BrowserSession(browser_config) as browser,
            bind_opensesame(browser, handoff_config) as session,
        ):
            page = await session.new_page(f"{base_url}/login")
            print(f"OpenSesame UI:  {args.opensesame_url}/#events")
            print(f"Fixture login:  {base_url}/login")
            print(f"Credentials:    {FIXTURE_USERNAME} / {FIXTURE_PASSWORD}")
            print("Solve the challenge over noVNC, then click Resolved.")

            result = await session.require_human(
                page,
                code="captcha.human_required",
                summary="Solve the local fixture challenge and sign in.",
                kind="captcha",
                subkind="fixture_visual",
                title="Local captcha login fixture",
                ttl_seconds=args.timeout,
            )

            final_url = await page.url()
            cookies = await page.get_cookies()
            authenticated = final_url.rstrip("/") == f"{base_url}/secure" and any(
                cookie["name"] == "opensesame_fixture"
                and cookie["value"] == "approved"
                and cookie["httpOnly"]
                for cookie in cookies
            )
            print(json.dumps(result.model_dump(), indent=2))
            print(json.dumps({"authenticated": authenticated, "url": final_url}))
            if not authenticated:
                raise SystemExit("Fixture login was not completed before resolution")


if __name__ == "__main__":
    asyncio.run(main())
