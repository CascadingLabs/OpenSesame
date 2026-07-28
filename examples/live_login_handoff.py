#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "yosoi==0.0.3a21",
#   "voidcrawl==0.3.8.2",
# ]
# ///
"""Live credential-handoff example: VoidCrawl + Yosoi + OpenSesame.

Why this example exists
-----------------------
Real scraping jobs often hit a login wall, SSO prompt, 2FA, cookie-consent
workflow, or other stateful browser step where the agent should *not* collect
or store credentials. This example pauses the job, sends an OpenSesame
interrupt, lets a human log in inside the live browser, then resumes the same
VoidCrawl tab and checks the authenticated page.

Live demo target
----------------
https://the-internet.herokuapp.com/login is a public Selenium demo page. It
prints test credentials on the page itself, so do not use real secrets here.
The point is the control flow: credentials are typed only into Chrome, never
passed to OpenSesame, Yosoi, or this script.

Latest PyPI versions checked with Yosoi on 2026-07-05:
- yosoi==0.0.3a21
- voidcrawl==0.3.8.2

Run from the OpenSesame checkout:

    # terminal 1, from this OpenSesame repo
    ../VoidCrawl/docker/run-browser.sh

    # terminal 2, from this OpenSesame repo
    uv run opensesame serve --no-notify --no-open-on-event --no-open-prompt

    # terminal 3, from this OpenSesame repo
    uv run --script examples/live_login_handoff.py

Then in remote browser (default http://127.0.0.1:3069):
1. Log in with the public demo credentials shown on the page:
   username: tomsmith
   password: SuperSecretPassword!
2. In OpenSesame, mark the interrupt resolved.
3. This script resumes the same tab and verifies the secure area.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib import error, parse, request
from uuid import uuid4

LATEST_YOSOI = "0.0.3a21"
LATEST_VOIDCRAWL = "0.3.8.2"
DEFAULT_LOGIN_URL = "https://the-internet.herokuapp.com/login"
DEFAULT_DOCKER_CDP_VERSION_URL = "http://127.0.0.1:19222/json/version"
DEFAULT_HANDOFF_URL = "http://127.0.0.1:3069"
DEFAULT_REMOTE_BROWSER_URL = DEFAULT_HANDOFF_URL
DEFAULT_KASMVNC_URL = DEFAULT_HANDOFF_URL
DEFAULT_NOVNC_URL: str | None = None
DEFAULT_VNC_URL: str | None = None


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def json_request(
    method: str, url: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method=method,
    )
    try:
        with request.urlopen(req, timeout=15) as response:  # noqa: S310 - local/user URL
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc


def resolve_docker_ws_url(version_url: str) -> str:
    try:
        payload = json_request("GET", version_url)
    except Exception as exc:
        raise RuntimeError(
            "Could not reach the VoidCrawl browser appliance at "
            f"{version_url}. Start it with: ../VoidCrawl/docker/run-browser.sh"
        ) from exc
    ws_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        raise RuntimeError(f"no webSocketDebuggerUrl in {version_url}: {payload!r}")
    return normalize_ws_url(version_url, ws_url)


def normalize_ws_url(version_url: str, ws_url: str) -> str:
    version = parse.urlparse(version_url)
    parsed = parse.urlparse(ws_url)
    if not parsed.path:
        return ws_url
    scheme = "wss" if version.scheme == "https" else "ws"
    return parse.urlunparse((scheme, version.netloc, parsed.path, "", parsed.query, ""))


def build_login_interrupt(
    *,
    event_id: str,
    url: str,
    websocket_url: str,
    target_id: str,
    yosoi_version: str,
    voidcrawl_version: str,
    handoff_url: str | None,
    remote_browser_url: str | None,
    kasmvnc_url: str | None,
    novnc_url: str | None,
    vnc_url: str | None,
) -> dict[str, Any]:
    return {
        "version": "interrupt.v1",
        "status": "waiting",
        "interrupt": {
            "id": event_id,
            "source": "opensesame.examples.live_login_handoff",
            "kind": "login_required",
            "subkind": "human_credentials",
            "blocking": True,
            "url": url,
            "attach": {
                "websocket_url": websocket_url,
                "target_id": target_id,
                "session_id": "live-login-handoff",
                "handoff_url": handoff_url or remote_browser_url or kasmvnc_url,
                "remote_browser_url": remote_browser_url or handoff_url or kasmvnc_url,
                "kasmvnc_url": kasmvnc_url,
                "novnc_url": novnc_url,
                "vnc_url": vnc_url,
            },
            "evidence": {
                "source": "login-wall-detected",
                "data": {
                    "credential_policy": "human-types-credentials-in-browser-only",
                    "secrets_stored_by_example": False,
                    "yosoi_version": yosoi_version,
                    "voidcrawl_version": voidcrawl_version,
                    "expected_yosoi_version": LATEST_YOSOI,
                    "expected_voidcrawl_version": LATEST_VOIDCRAWL,
                },
            },
            "resume_hint": {
                "strategy": "same_tab_after_human_login",
                "context": {"success_url_contains": "/secure"},
            },
        },
    }


async def wait_for_resolution(
    *,
    base_url: str,
    event_id: str,
    timeout_s: float,
    poll_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    detail_url = f"{base_url.rstrip('/')}/api/interrupts/{event_id}"
    while time.monotonic() < deadline:
        event = await asyncio.to_thread(json_request, "GET", detail_url)
        payload = event.get("event", {})
        if payload.get("status") != "pending":
            return payload
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"OpenSesame event {event_id} was not resolved in {timeout_s}s")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opensesame-url", default="http://127.0.0.1:8765")
    parser.add_argument("--login-url", default=DEFAULT_LOGIN_URL)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--docker-version-url", default=DEFAULT_DOCKER_CDP_VERSION_URL)
    parser.add_argument("--handoff-url", default=DEFAULT_HANDOFF_URL)
    parser.add_argument("--remote-browser-url", default=DEFAULT_REMOTE_BROWSER_URL)
    parser.add_argument("--kasmvnc-url", default=None)
    parser.add_argument("--novnc-url", default=DEFAULT_NOVNC_URL)
    parser.add_argument("--vnc-url", default=DEFAULT_VNC_URL)
    parser.add_argument(
        "--local-headful",
        action="store_true",
        help="Launch local Chrome instead of using the VoidCrawl browser appliance",
    )
    args = parser.parse_args()

    import yosoi as ys
    from voidcrawl import BrowserConfig, BrowserSession

    yosoi_version = package_version("yosoi")
    voidcrawl_version = package_version("voidcrawl")
    print(f"imported yosoi=={yosoi_version} (latest checked: {LATEST_YOSOI})")
    print(
        f"imported voidcrawl=={voidcrawl_version} "
        f"(latest checked: {LATEST_VOIDCRAWL})"
    )

    # Yosoi first: prove the chosen live page is reachable and capture metadata
    # without ever handling credentials.
    preview = await ys.fetch(args.login_url, view="metadata", fetcher_type="simple")
    unit = preview.results[0]
    print(
        "Yosoi preview:",
        json.dumps(
            {
                "status": preview.status,
                "unit_status": unit.status,
                "status_code": unit.status_code,
                "title": unit.title,
                "url": unit.url,
            },
            indent=2,
        ),
    )

    if args.local_headful:
        config = BrowserConfig(headless=False)
        print(
            "using local headful Chrome; OpenSesame will not get "
            "a remote browser URL"
        )
        handoff_url = None
        remote_browser_url = None
        kasmvnc_url = None
        novnc_url = None
        vnc_url = None
    else:
        docker_ws_url = resolve_docker_ws_url(args.docker_version_url)
        config = BrowserConfig(ws_url=docker_ws_url, headless=False)
        print(f"using VoidCrawl browser appliance: {args.docker_version_url}")
        print(f"operator remote browser: {args.handoff_url}")
        handoff_url = args.handoff_url
        remote_browser_url = args.remote_browser_url
        kasmvnc_url = args.kasmvnc_url
        novnc_url = args.novnc_url
        vnc_url = args.vnc_url
    async with BrowserSession(config) as browser:
        page = await browser.new_page("about:blank")
        try:
            await page.goto(args.login_url, timeout=30.0)
        except Exception as exc:
            print(f"navigation did not fully settle: {exc}", file=sys.stderr)

        event_id = f"login-handoff-{uuid4()}"
        payload = build_login_interrupt(
            event_id=event_id,
            url=args.login_url,
            websocket_url=await browser.websocket_url(),
            target_id=await page.target_id(),
            yosoi_version=yosoi_version,
            voidcrawl_version=voidcrawl_version,
            handoff_url=handoff_url,
            remote_browser_url=remote_browser_url,
            kasmvnc_url=kasmvnc_url,
            novnc_url=novnc_url,
            vnc_url=vnc_url,
        )
        endpoint = f"{args.opensesame_url.rstrip('/')}/api/interrupts"
        created = await asyncio.to_thread(json_request, "POST", endpoint, payload)
        print(json.dumps(created, indent=2))
        print(f"OpenSesame UI: {args.opensesame_url}/#event-{event_id}")
        if handoff_url:
            print(f"remote browser: {handoff_url}")
        if novnc_url:
            print(f"legacy noVNC: {novnc_url}")
        print(
            "Type credentials into the browser only; then mark resolved in "
            "OpenSesame."
        )

        resolved = await wait_for_resolution(
            base_url=args.opensesame_url,
            event_id=event_id,
            timeout_s=args.timeout,
            poll_s=args.poll,
        )
        print("OpenSesame resolution:", json.dumps(resolved, indent=2))

        current_url = await page.url()
        title = await page.title()
        body_text = await page.evaluate_js(
            "document.body ? document.body.innerText : ''"
        )
        authenticated = "/secure" in str(current_url) and "Secure Area" in str(
            body_text
        )
        print(
            json.dumps(
                {
                    "resumed_same_tab": True,
                    "current_url": current_url,
                    "title": title,
                    "authenticated": authenticated,
                    "credential_storage": "none: human typed directly into browser",
                },
                indent=2,
            )
        )
        if not authenticated:
            raise SystemExit("Login was not detected after OpenSesame resolution")


if __name__ == "__main__":
    asyncio.run(main())
