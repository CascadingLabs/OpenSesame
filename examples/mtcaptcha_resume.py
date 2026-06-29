"""Human takeover and resume with VoidCrawl + OpenSesame.

This example uses the public MTCaptcha demo page because it is a small,
repeatable challenge that benefits from a real human in the live browser.

Flow:

1. Start VoidCrawl's headful Docker browser so there is a visible noVNC session.
2. Navigate one VoidCrawl tab to https://2captcha.com/demo/mtcaptcha.
3. Queue that same tab in OpenSesame with VNC/noVNC links and capture evidence.
4. Solve the challenge manually in noVNC, then click Resolved in OpenSesame.
5. Resume automation in the original tab and print the after-state.

Run from this OpenSesame checkout:

    # terminal 1, from ../VoidCrawl
    ./docker/run-headful.sh

    # terminal 2, from this repo
    uv run python examples/mtcaptcha_resume.py --open-ui

The point is the handoff boundary: automation does not open a new browser after
human help; it keeps using the same VoidCrawl page object.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any
from uuid import uuid4

from voidcrawl import BrowserConfig, BrowserSession

from opensesame.demo import (
    DEFAULT_DOCKER_CDP_VERSION_URL,
    DEFAULT_NOVNC_URL,
    DEFAULT_OPENSESAME_URL,
    DEFAULT_VNC_URL,
    browser_config,
    capture_voidcrawl_challenge,
    post_json,
    start_opensesame_server,
)
from opensesame.storage import DEFAULT_DB_PATH, TakeoverStore

DEFAULT_TARGET_URL = "https://2captcha.com/demo/mtcaptcha"
DEFAULT_SCREENSHOT_DIR = Path(".opensesame/mtcaptcha-example")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a natural VoidCrawl -> OpenSesame human takeover example "
            "against the 2Captcha MTCaptcha demo."
        )
    )
    parser.add_argument("--url", default=DEFAULT_TARGET_URL)
    parser.add_argument("--opensesame-url", default=DEFAULT_OPENSESAME_URL)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--takeover-timeout", type=float, default=300.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--novnc-url", default=DEFAULT_NOVNC_URL)
    parser.add_argument("--vnc-url", default=DEFAULT_VNC_URL)
    parser.add_argument("--docker-version-url", default=DEFAULT_DOCKER_CDP_VERSION_URL)
    parser.add_argument("--screenshot-dir", type=Path, default=DEFAULT_SCREENSHOT_DIR)
    parser.add_argument(
        "--local-headful",
        action="store_true",
        help=(
            "Launch/connect to local Chrome on --port instead of Docker headful Chrome."
        ),
    )
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument(
        "--no-serve-ui",
        action="store_true",
        help="Do not start OpenSesame; require an existing server at --opensesame-url.",
    )
    parser.add_argument("--open-ui", action="store_true")
    return parser.parse_args()


def get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} returned non-object JSON")
    return payload


async def wait_for_open_sesame_resolution(
    *,
    opensesame_url: str,
    event_id: str,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    detail_url = f"{opensesame_url.rstrip('/')}/api/takeovers/{event_id}"
    while True:
        payload = await asyncio.to_thread(get_json, detail_url)
        event = payload.get("event")
        if not isinstance(event, dict):
            raise RuntimeError(f"invalid OpenSesame detail payload: {payload!r}")
        if event.get("status") != "pending":
            return event
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(
                f"timed out waiting for OpenSesame to resolve {event_id}"
            )
        await asyncio.sleep(poll_interval)


def summarize_capture(capture: dict[str, Any]) -> dict[str, Any]:
    challenge = capture["challenge"]
    attach = challenge.get("attach_coordinates") or {}
    return {
        "event_id": challenge.get("event_id"),
        "blocking": challenge.get("blocking"),
        "url": challenge.get("url"),
        "status_code": challenge.get("status_code"),
        "antibot": challenge.get("antibot"),
        "dom_captcha": challenge.get("dom_captcha"),
        "target_id": attach.get("target_id"),
        "novnc_url": attach.get("novnc_url"),
        "vnc_url": attach.get("vnc_url"),
    }


async def run_mtcaptcha_resume(args: argparse.Namespace) -> None:
    args.screenshot_dir.mkdir(parents=True, exist_ok=True)
    store = TakeoverStore(args.db)
    await store.init()

    server_process = None
    if not args.no_serve_ui:
        server_process = start_opensesame_server(
            opensesame_url=args.opensesame_url,
            db_path=args.db,
        )

    config: BrowserConfig = browser_config(
        docker_headful=not args.local_headful,
        port=args.port,
        docker_version_url=args.docker_version_url,
    )

    try:
        if args.open_ui:
            webbrowser.open(args.opensesame_url)

        async with BrowserSession(config) as browser:
            page = await browser.new_page("about:blank")
            print(f"navigating: {args.url}")
            nav_error: str | None = None
            try:
                await page.goto(args.url, timeout=args.timeout)
            except Exception as exc:  # live site may keep challenge long-polling
                nav_error = str(exc)
                print(f"navigation did not fully settle: {nav_error}")

            before_png = args.screenshot_dir / "before.png"
            before_png.write_bytes(await page.screenshot_png())

            capture = await capture_voidcrawl_challenge(
                browser=browser,
                page=page,
                session_id="mtcaptcha-example",
                vnc_url=args.vnc_url,
                novnc_url=args.novnc_url,
            )
            challenge = capture["challenge"]
            if nav_error and not challenge.get("ax_summary"):
                challenge["ax_summary"] = nav_error
            event_id = str(challenge.get("event_id") or uuid4())
            challenge["event_id"] = event_id

            print("\nchallenge capture")
            print(json.dumps(summarize_capture(capture), indent=2))

            if not challenge.get("blocking"):
                print(
                    "\nVoidCrawl did not classify this as blocking; queuing a "
                    "manual takeover anyway for this MTCaptcha example."
                )
                capture["operator_hint"] = (
                    capture.get("operator_hint")
                    or "Manual MTCaptcha takeover requested by example script."
                )

            endpoint = f"{args.opensesame_url.rstrip('/')}/api/voidcrawl/challenge"
            await asyncio.to_thread(post_json, endpoint, capture)
            print(f"\nsent to OpenSesame: {endpoint}")
            print(f"OpenSesame UI: {args.opensesame_url}/#event-{event_id}")
            print(f"noVNC:        {args.novnc_url}")
            print(f"VNC:          {args.vnc_url}")
            print("Solve MTCaptcha in that same browser, then click Resolved.")

            resolved_event = await wait_for_open_sesame_resolution(
                opensesame_url=args.opensesame_url,
                event_id=event_id,
                timeout=args.takeover_timeout,
                poll_interval=args.poll_interval,
            )

            try:
                await page.wait_for_network_idle(timeout=5.0)
            except Exception as exc:  # diagnostic only on live web
                print(f"network idle wait did not complete: {exc}")

            captcha_after = await page.detect_captcha()
            after_png = args.screenshot_dir / "after.png"
            after_png.write_bytes(await page.screenshot_png())

            result = {
                "event_id": event_id,
                "resolver": resolved_event.get("resolver"),
                "status": resolved_event.get("status"),
                "captcha_after": captcha_after,
                "captcha_cleared": captcha_after is None,
                "title_after": await page.title(),
                "url_after": await page.url(),
                "before_screenshot": str(before_png),
                "after_screenshot": str(after_png),
            }
            print("\nresume result")
            print(json.dumps(result, indent=2))
            if captcha_after is not None:
                raise SystemExit(f"still blocked by captcha kind: {captcha_after}")
    finally:
        if server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=3)
            except Exception:
                server_process.kill()


def main() -> None:
    try:
        asyncio.run(run_mtcaptcha_resume(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
