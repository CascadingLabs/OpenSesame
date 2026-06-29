from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console
from voidcrawl import BrowserConfig, BrowserSession

from opensesame.storage import DEFAULT_DB_PATH, TakeoverStore

DEMO_TARGETS = {
    "cloudflare": "https://2captcha.com/demo/cloudflare-turnstile",
    "turnstile": "https://2captcha.com/demo/cloudflare-turnstile",
    "recaptcha": "https://2captcha.com/demo/recaptcha-v2",
}
DEFAULT_URL = DEMO_TARGETS["cloudflare"]
DEFAULT_OPENSESAME_URL = "http://127.0.0.1:8765"
DEFAULT_NOVNC_URL = "http://127.0.0.1:6080"
DEFAULT_VNC_URL = "vnc://127.0.0.1:5900"
DEFAULT_DOCKER_CDP_VERSION_URL = "http://127.0.0.1:19222/json/version"

console = Console()


def resolve_docker_ws_url(version_url: str) -> str:
    with urllib.request.urlopen(version_url, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ws_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        raise RuntimeError(f"no webSocketDebuggerUrl in {version_url}")
    return ws_url


def browser_config(
    *, docker_headful: bool, port: int, docker_version_url: str
) -> BrowserConfig:
    if docker_headful:
        return BrowserConfig(
            ws_url=resolve_docker_ws_url(docker_version_url), headless=False
        )
    return BrowserConfig(headless=False, port=port)


async def capture_voidcrawl_challenge(
    *,
    browser: BrowserSession,
    page: Any,
    session_id: str,
    vnc_url: str,
    novnc_url: str,
) -> dict[str, Any]:
    websocket_url = await browser.websocket_url()
    capture = await page.capture_challenge(
        websocket_url=websocket_url,
        session_id=session_id,
        vnc_url=vnc_url,
        novnc_url=novnc_url,
    )
    if not isinstance(capture, dict) or not isinstance(capture.get("challenge"), dict):
        raise RuntimeError("VoidCrawl returned an invalid challenge capture envelope")
    return capture


async def should_open_ui(ui_prompt: bool) -> bool:
    if not ui_prompt or not sys.stdin.isatty():
        return False
    answer = await asyncio.to_thread(
        console.input,
        "Press [bold]o[/]+Enter to open OpenSesame, or Enter to continue: ",
    )
    return answer.strip().lower() == "o"


def url_is_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            status = int(response.status)
            return 200 <= status < 500
    except Exception:
        return False


def start_opensesame_server(
    *, opensesame_url: str, db_path: Path, notify: bool = False
) -> subprocess.Popen[str] | None:
    if url_is_ready(opensesame_url):
        return None

    parsed = urllib.parse.urlparse(opensesame_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765
    executable = shutil.which("opensesame") or sys.argv[0]
    command = [
        executable,
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--public-url",
        opensesame_url,
        "--db",
        str(db_path),
        "--no-open-on-event",
        "--no-open-prompt",
    ]
    if not notify:
        command.append("--no-notify")

    console.print(f"[dim]starting OpenSesame server:[/] {opensesame_url}")
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    for _ in range(40):
        if process.poll() is not None:
            raise RuntimeError("OpenSesame server exited during startup")
        if url_is_ready(opensesame_url):
            return process
        import time

        time.sleep(0.25)
    process.terminate()
    raise RuntimeError(f"OpenSesame server did not become ready: {opensesame_url}")


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("OpenSesame returned a non-object JSON response")
    return data


async def run_demo(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    challenge_type: str = "cloudflare",
    url: str | None = None,
    opensesame_url: str = DEFAULT_OPENSESAME_URL,
    port: int = 9222,
    docker_headful: bool = False,
    docker_version_url: str = DEFAULT_DOCKER_CDP_VERSION_URL,
    novnc_url: str = DEFAULT_NOVNC_URL,
    vnc_url: str = DEFAULT_VNC_URL,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
    open_ui: bool = False,
    ui_prompt: bool = True,
    serve_ui: bool = True,
) -> None:
    store = TakeoverStore(db_path)
    await store.init()
    target_url = url or DEMO_TARGETS[challenge_type]
    server_process = (
        start_opensesame_server(opensesame_url=opensesame_url, db_path=db_path)
        if serve_ui
        else None
    )
    console.print(f"OpenSesame UI: [link={opensesame_url}]{opensesame_url}[/link]")
    if open_ui or await should_open_ui(ui_prompt):
        webbrowser.open(opensesame_url)
    config = browser_config(
        docker_headful=docker_headful,
        port=port,
        docker_version_url=docker_version_url,
    )

    console.print(f"[bold]navigating[/] {target_url}")
    try:
        await drive_demo_browser(
            config=config,
            target_url=target_url,
            challenge_type=challenge_type,
            opensesame_url=opensesame_url,
            vnc_url=vnc_url,
            novnc_url=novnc_url,
            timeout=timeout,
            poll_interval=poll_interval,
            store=store,
        )
    finally:
        if server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_process.kill()


async def drive_demo_browser(
    *,
    config: BrowserConfig,
    target_url: str,
    challenge_type: str,
    opensesame_url: str,
    vnc_url: str,
    novnc_url: str,
    timeout: float,
    poll_interval: float,
    store: TakeoverStore,
) -> None:
    async with BrowserSession(config) as browser:
        page = await browser.new_page("about:blank")
        nav_error: str | None = None
        try:
            await page.goto(target_url, timeout=timeout)
        except Exception as exc:  # pragma: no cover - depends on demo site timing
            nav_error = str(exc)
            console.print(f"[yellow]navigation did not fully settle:[/] {nav_error}")
        capture_payload = await capture_voidcrawl_challenge(
            browser=browser,
            page=page,
            session_id="opensesame-demo",
            vnc_url=vnc_url,
            novnc_url=novnc_url,
        )
        challenge = capture_payload["challenge"]
        if nav_error and not challenge.get("ax_summary"):
            challenge["ax_summary"] = nav_error
        if not challenge.get("blocking"):
            console.print(f"[yellow]{capture_payload.get('operator_hint')}[/]")
            return
        event_id = str(challenge.get("event_id") or uuid4())

        endpoint = f"{opensesame_url.rstrip('/')}/api/voidcrawl/challenge"
        await asyncio.to_thread(post_json, endpoint, capture_payload)

        console.print(f"[green]sent VoidCrawl interrupt[/] {event_id} -> {endpoint}")
        console.print(
            f"Open OpenSesame: [link={opensesame_url}]{opensesame_url}[/link]"
        )
        console.print(f"VNC:   {vnc_url}")
        console.print(f"noVNC: {novnc_url}")
        console.print("Solve in the same browser, then click [bold]Mark resolved[/].")

        while True:
            current = await store.get_event(event_id)
            if current and current.status != "pending":
                break
            await asyncio.sleep(poll_interval)

        try:
            await page.wait_for_network_idle(timeout=5.0)
        except Exception as exc:  # pragma: no cover - diagnostic only
            console.print(f"[yellow]network idle wait did not complete:[/] {exc}")
        try:
            captcha_after = await asyncio.wait_for(page.detect_captcha(), timeout=3.0)
        except Exception as exc:  # pragma: no cover - demo resilience
            captcha_after = None
            console.print(f"[yellow]final captcha probe did not complete:[/] {exc}")
        console.print(
            "[green]resolved[/]"
            if captcha_after is None
            else "[yellow]marked resolved, captcha still detected[/]"
        )
        try:
            final_after_url = await page.url()
        except Exception as exc:  # pragma: no cover - browser may close on teardown
            final_after_url = f"unavailable: {exc}"
        console.print(f"final url: {final_after_url}")
