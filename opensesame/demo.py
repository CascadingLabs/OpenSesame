from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

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


def antibot_to_dict(response: Any) -> dict[str, Any] | None:
    antibot = getattr(response, "antibot", None)
    if antibot is None:
        return None
    return {
        "vendors": list(getattr(antibot, "vendors", []) or []),
        "challenged": bool(getattr(antibot, "challenged", False)),
        "challenge_vendor": getattr(antibot, "challenge_vendor", None),
        "corpus_version": getattr(antibot, "corpus_version", None),
        "evidence": getattr(antibot, "evidence", None),
    }


async def should_open_ui(ui_prompt: bool) -> bool:
    if not ui_prompt or not sys.stdin.isatty():
        return False
    answer = await asyncio.to_thread(
        console.input,
        "Press [bold]o[/]+Enter to open OpenSesame, or Enter to continue: ",
    )
    return answer.strip().lower() == "o"


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
) -> None:
    store = TakeoverStore(db_path)
    await store.init()
    target_url = url or DEMO_TARGETS[challenge_type]
    console.print(
        f"OpenSesame UI: [link={opensesame_url}]{opensesame_url}[/link]"
    )
    if open_ui or await should_open_ui(ui_prompt):
        webbrowser.open(opensesame_url)
    config = browser_config(
        docker_headful=docker_headful,
        port=port,
        docker_version_url=docker_version_url,
    )

    console.print(f"[bold]navigating[/] {target_url}")
    async with BrowserSession(config) as browser:
        page = await browser.new_page("about:blank")
        nav_error: str | None = None
        try:
            response = await page.goto(target_url, timeout=timeout)
        except Exception as exc:  # pragma: no cover - depends on demo site timing
            response = None
            nav_error = str(exc)
            console.print(f"[yellow]navigation did not fully settle:[/] {nav_error}")
        captcha = await page.detect_captcha()
        final_url = await page.url()
        target_id = await page.target_id()
        websocket_url = await browser.websocket_url()
        event_id = f"demo-{challenge_type}-{target_id or 'takeover'}"
        captcha_kind = str(captcha) if captcha else challenge_type

        capture_payload = {
            "operator_hint": "Open VNC/noVNC, solve in-place, then mark resolved.",
            "challenge": {
                "event_id": event_id,
                "url": final_url,
                "status_code": getattr(response, "status_code", None),
                "status": "active",
                "blocking": True,
                "antibot": antibot_to_dict(response),
                "dom_captcha": {
                    "kind": captcha_kind,
                    "page_url": final_url,
                    "active": True,
                    "widget_rendered": captcha is not None,
                },
                "ax_summary": nav_error,
                "attach_coordinates": {
                    "websocket_url": websocket_url,
                    "target_id": target_id,
                    "session_id": "opensesame-demo",
                    "vnc_url": vnc_url,
                    "novnc_url": novnc_url,
                },
            },
        }
        endpoint = f"{opensesame_url.rstrip('/')}/api/voidcrawl/challenge"
        await asyncio.to_thread(post_json, endpoint, capture_payload)

        console.print(f"[green]sent interrupt[/] {event_id} -> {endpoint}")
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
        captcha_after = await page.detect_captcha()
        console.print(
            "[green]resolved[/]"
            if captcha_after is None
            else "[yellow]marked resolved, captcha still detected[/]"
        )
        console.print(f"final url: {await page.url()}")
