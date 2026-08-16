#!/usr/bin/env python3
"""Run a local mock-login interrupt through noVNC and OpenSesame.

This starts a disposable HTTP login site on the host, opens it in VoidCrawl's
headful Docker Chrome, and parks the exact tab. Log in through the noVNC frame
in OpenSesame, then click **Resolved**. The caller verifies that the same tab
reached ``/secure`` and retained its HttpOnly session cookie.

Run from the OpenSesame checkout while VoidCrawl's headful container is running:

    # terminal 1
    uv run --no-sync opensesame watch

    # terminal 2
    PYTHONPATH=../VoidCrawl \
      uv run --no-sync python examples/mock_login_interrupt_handoff.py

Mock credentials (safe and local only):

    username: operator
    password: open-sesame
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse, request
from uuid import uuid4

from opensesame.voidcrawl_interrupts import (
    VoidCrawlHandoffConfig,
    VoidCrawlInterruptHandoff,
)

MOCK_USERNAME = "operator"
MOCK_PASSWORD = "open-sesame"
DEFAULT_CDP_VERSION_URL = "http://127.0.0.1:19222/json/version"
DEFAULT_NOVNC_URL = "http://127.0.0.1:6080"


class MockLoginHandler(BaseHTTPRequestHandler):
    server_version = "OpenSesameMockLogin/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/login":
            self._html(
                """<!doctype html>
                <title>OpenSesame Mock Login</title>
                <main><h1>Mock Login</h1>
                <p>Use the local, disposable credentials from the script terminal.</p>
                <form method="post" action="/login">
                  <label>Username
                    <input name="username" autocomplete="username">
                  </label>
                  <label>Password
                    <input name="password" type="password"
                           autocomplete="current-password">
                  </label>
                  <button type="submit">Sign in</button>
                </form></main>"""
            )
            return
        if self.path == "/secure":
            if "mock_session=approved" not in self.headers.get("Cookie", ""):
                self._redirect("/login")
                return
            self._html(
                "<!doctype html><title>Mock Secure Area</title>"
                "<main><h1>Mock Secure Area</h1><p>Authenticated locally.</p></main>"
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/login":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        form = parse.parse_qs(self.rfile.read(content_length).decode("utf-8"))
        if (
            form.get("username", [None])[0] == MOCK_USERNAME
            and form.get("password", [None])[0] == MOCK_PASSWORD
        ):
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header(
                "Set-Cookie", "mock_session=approved; HttpOnly; SameSite=Lax; Path=/"
            )
            self.send_header("Location", "/secure")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._redirect("/login")

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep mock login requests out of the caller's result output."""

    def _html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


@contextmanager
def mock_login_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockLoginHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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
    from voidcrawl import BrowserConfig, BrowserSession, InterruptRequest

    ws_url = resolve_browser_ws_url(args.docker_version_url)
    with mock_login_server() as base_url:
        handoff = VoidCrawlInterruptHandoff(
            VoidCrawlHandoffConfig(
                opensesame_url=args.opensesame_url,
                session_id=f"mock-login-{uuid4()}",
                novnc_url=args.novnc_url,
                resolution_timeout_seconds=args.timeout,
            )
        )
        browser_config = BrowserConfig(ws_url=ws_url, headless=False)
        async with BrowserSession(browser_config) as browser:
            page = await browser.new_page(f"{base_url}/login")
            print(f"OpenSesame UI: {args.opensesame_url}/#events")
            print(f"Mock login URL: {base_url}/login")
            print(f"Mock credentials: {MOCK_USERNAME} / {MOCK_PASSWORD}")
            print("Log in through noVNC, wait for /secure, then click Resolved.")
            result = await handoff.interrupt_and_wait(
                browser,
                page,
                InterruptRequest(
                    code="authentication.human_required",
                    summary="Complete the local mock login in noVNC.",
                    ttl_seconds=args.timeout,
                ),
                kind="authentication",
                subkind="mock_login",
                title="Local mock login",
            )
            cookies = await page.get_cookies()
            authenticated = (await page.url()).rstrip(
                "/"
            ) == f"{base_url}/secure" and any(
                cookie["name"] == "mock_session"
                and cookie["value"] == "approved"
                and cookie["httpOnly"]
                for cookie in cookies
            )
            print(json.dumps(result.model_dump(), indent=2))
            print(json.dumps({"authenticated": authenticated, "url": await page.url()}))
            if not authenticated:
                raise SystemExit("Mock login was not completed before resolution")


if __name__ == "__main__":
    asyncio.run(main())
