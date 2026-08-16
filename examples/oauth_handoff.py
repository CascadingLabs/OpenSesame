#!/usr/bin/env python3
"""Generic OAuth human-authorization interrupt example.

Use this when an agent reaches an OAuth/SSO consent screen or device-auth step
that should be completed by a human, not by automation that stores credentials
or secrets.

This example is provider-neutral. It posts an ``interrupt.v1`` envelope to
OpenSesame, waits for the operator to complete authorization in the live browser
or another approved browser, then resumes once OpenSesame is marked resolved.

Run from this OpenSesame checkout:

    # terminal 1
    uv run opensesame serve --no-notify --no-open-on-event --no-open-prompt

    # terminal 2: queue a safe demo OAuth interruption
    uv run python examples/oauth_handoff.py \
      --authorization-url 'https://accounts.example.test/oauth/authorize?client_id=demo&response_type=code&scope=read'

For a real provider, pass the current authorization URL or device-verification
URL. Do not include client secrets. By default, query parameters are redacted
before the URL is stored in OpenSesame; pass ``--store-query`` only for test
fixtures where the query is known safe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.parse
import webbrowser
from typing import Any
from urllib import error, request
from uuid import uuid4

DEFAULT_AUTHORIZATION_URL = (
    "https://accounts.example.test/oauth/authorize"
    "?client_id=demo&response_type=code&scope=read"
)


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
        with request.urlopen(req, timeout=15) as response:  # noqa: S310 - user URL
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc


def redacted_url(raw_url: str, *, store_query: bool = False) -> str:
    parsed = urllib.parse.urlparse(raw_url)
    query = parsed.query if store_query else ""
    fragment = parsed.fragment if store_query else ""
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, fragment)
    )


def build_oauth_interrupt(
    *,
    event_id: str,
    authorization_url: str,
    provider: str,
    session_id: str,
    store_query: bool = False,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(authorization_url)
    query = urllib.parse.parse_qs(parsed.query)
    return {
        "version": "interrupt.v1",
        "status": "waiting",
        "interrupt": {
            "id": event_id,
            "source": "opensesame.examples.oauth_handoff",
            "kind": "oauth",
            "subkind": "human_authorization",
            "blocking": True,
            "url": redacted_url(authorization_url, store_query=store_query),
            "attach": {"session_id": session_id},
            "evidence": {
                "source": "oauth.authorization_required",
                "data": {
                    "provider": provider,
                    "authorization_host": parsed.netloc,
                    "authorization_path": parsed.path,
                    "query_redacted": not store_query,
                    "scope_present": "scope" in query,
                    "state_present": "state" in query,
                    "client_secret_stored": False,
                },
            },
            "resume_hint": {
                "strategy": "continue_after_callback_or_device_confirmation",
                "context": {
                    "authorization_host": parsed.netloc,
                    "operator_must_not_share_secrets_with_agent": True,
                },
            },
            "metadata": {
                "original_url_query_redacted": not store_query,
                "example": True,
            },
        },
    }


async def wait_for_resolution(
    *, base_url: str, event_id: str, timeout_s: float, poll_s: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    detail_url = f"{base_url.rstrip('/')}/api/interrupts/{event_id}"
    while time.monotonic() < deadline:
        response = await asyncio.to_thread(json_request, "GET", detail_url)
        event = response.get("event", {})
        if event.get("status") != "pending":
            return event
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"OpenSesame event {event_id} was not resolved in {timeout_s}s")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opensesame-url", default="http://127.0.0.1:8765")
    parser.add_argument("--authorization-url", default=DEFAULT_AUTHORIZATION_URL)
    parser.add_argument("--provider", default="example-oauth")
    parser.add_argument("--session-id", default="oauth-handoff")
    parser.add_argument("--event-id", default=None)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument(
        "--store-query",
        action="store_true",
        help="Store the authorization URL query in OpenSesame; off by default.",
    )
    parser.add_argument(
        "--open-auth-url",
        action="store_true",
        help="Open the raw authorization URL locally after queueing the event.",
    )
    args = parser.parse_args()

    event_id = args.event_id or f"oauth-handoff-{uuid4()}"
    payload = build_oauth_interrupt(
        event_id=event_id,
        authorization_url=args.authorization_url,
        provider=args.provider,
        session_id=args.session_id,
        store_query=args.store_query,
    )
    endpoint = f"{args.opensesame_url.rstrip('/')}/api/interrupts"
    created = await asyncio.to_thread(json_request, "POST", endpoint, payload)
    print(json.dumps(created, indent=2))
    print(f"OpenSesame UI: {args.opensesame_url}/#event-{event_id}")
    if args.open_auth_url:
        webbrowser.open(args.authorization_url)

    print("Complete OAuth/SSO as a human, then mark resolved in OpenSesame.")
    resolved = await wait_for_resolution(
        base_url=args.opensesame_url,
        event_id=event_id,
        timeout_s=args.timeout,
        poll_s=args.poll,
    )
    print("OpenSesame resolution:", json.dumps(resolved, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
