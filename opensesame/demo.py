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
    "clickcaptcha": "https://2captcha.com/demo/clickcaptcha",
    "cloudflare": "https://2captcha.com/demo/cloudflare-turnstile",
    "cloudflare-managed": "https://2captcha.com/demo/cloudflare-turnstile-challenge",
    "cloudflare-turnstile": "https://2captcha.com/demo/cloudflare-turnstile",
    "geetest": "https://2captcha.com/demo/geetest",
    "geetest-v4": "https://2captcha.com/demo/geetest-v4",
    "lemin": "https://2captcha.com/demo/lemin",
    "mtcaptcha": "https://2captcha.com/demo/mtcaptcha",
    "normal": "https://2captcha.com/demo/normal",
    "recaptcha": "https://2captcha.com/demo/recaptcha-v2",
    "recaptcha-v2": "https://2captcha.com/demo/recaptcha-v2",
    "recaptcha-v2-callback": "https://2captcha.com/demo/recaptcha-v2-callback",
    "recaptcha-v2-enterprise": "https://2captcha.com/demo/recaptcha-v2-enterprise",
    "recaptcha-v2-invisible": "https://2captcha.com/demo/recaptcha-v2-invisible",
    "recaptcha-v3": "https://2captcha.com/demo/recaptcha-v3",
    "recaptcha-v3-enterprise": "https://2captcha.com/demo/recaptcha-v3-enterprise",
    "rotatecaptcha": "https://2captcha.com/demo/rotatecaptcha",
    "text": "https://2captcha.com/demo/text",
    "turnstile": "https://2captcha.com/demo/cloudflare-turnstile",
    "xcaptcha-moving": "https://xcaptcha.com/demo",
    "xcaptcha-no-captcha": "https://xcaptcha.com/demo",
    "xcaptcha-text-click-v1": "https://xcaptcha.com/demo",
    "xcaptcha-text-click-v2": "https://xcaptcha.com/demo",
}

RECAPTCHA_TYPE_TARGETS = {
    "v2": "recaptcha-v2",
    "v2-callback": "recaptcha-v2-callback",
    "v2-enterprise": "recaptcha-v2-enterprise",
    "v2-invisible": "recaptcha-v2-invisible",
    "v3": "recaptcha-v3",
    "v3-enterprise": "recaptcha-v3-enterprise",
}
RECAPTCHA_DEMO_TARGETS = tuple(RECAPTCHA_TYPE_TARGETS.values())

CLOUDFLARE_TYPE_TARGETS = {
    "turnstile": "cloudflare-turnstile",
    "widget": "cloudflare-turnstile",
    "managed": "cloudflare-managed",
    "challenge": "cloudflare-managed",
}
CLOUDFLARE_DEMO_TARGETS = ("cloudflare-turnstile", "cloudflare-managed")


def xcaptcha_button(sitekey: str) -> str:
    return f'button[data-captcha-sitekey="{sitekey}"]'


DEMO_PREPARE_SELECTORS = {
    "xcaptcha-moving": xcaptcha_button("506195d06393f98584931a6ede3cb64c"),
    "xcaptcha-no-captcha": xcaptcha_button("a537c95d43097aed9cd8a295ecdc2a79"),
    "xcaptcha-text-click-v1": xcaptcha_button("5b4fc1a221c3e79c9bac190363808884"),
    "xcaptcha-text-click-v2": xcaptcha_button("11aa62606fb968f3674742df60598957"),
}
DEMO_ARM_TARGETS = RECAPTCHA_DEMO_TARGETS + CLOUDFLARE_DEMO_TARGETS
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


async def prepare_demo_target(page: Any, challenge_type: str) -> None:
    selector = DEMO_PREPARE_SELECTORS.get(challenge_type)
    if not selector:
        return
    quoted_selector = json.dumps(selector)
    deadline = asyncio.get_running_loop().time() + 10.0
    while True:
        clicked = await page.evaluate_js(
            "(() => {"
            f"const el = document.querySelector({quoted_selector});"
            "if (!el) return false;"
            "el.scrollIntoView({block: 'center', inline: 'center'});"
            "el.click();"
            "return true;"
            "})()"
        )
        if clicked:
            await asyncio.sleep(1.0)
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"XCaptcha demo button not found: {selector}")
        await asyncio.sleep(0.5)


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
    force_takeover: bool = False,
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
            force_takeover=force_takeover,
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
    force_takeover: bool = False,
) -> None:
    async with BrowserSession(config) as browser:
        page = await browser.new_page("about:blank")
        nav_error: str | None = None
        try:
            await page.goto(target_url, timeout=timeout)
        except Exception as exc:  # pragma: no cover - depends on demo site timing
            nav_error = str(exc)
            console.print(f"[yellow]navigation did not fully settle:[/] {nav_error}")
        await prepare_demo_target(page, challenge_type)
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
        if not challenge.get("blocking") and not force_takeover:
            console.print(f"[yellow]{capture_payload.get('operator_hint')}[/]")
            return
        if not challenge.get("blocking"):
            capture_payload["operator_hint"] = capture_payload.get("operator_hint") or (
                "Manual takeover requested even though VoidCrawl did not "
                "mark this as blocking."
            )
        event_id = str(challenge.get("event_id") or uuid4())
        challenge["event_id"] = event_id

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


async def arm_all_demo_events(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    opensesame_url: str = DEFAULT_OPENSESAME_URL,
    port: int = 9222,
    docker_headful: bool = True,
    docker_version_url: str = DEFAULT_DOCKER_CDP_VERSION_URL,
    novnc_url: str = DEFAULT_NOVNC_URL,
    vnc_url: str = DEFAULT_VNC_URL,
    timeout: float = 20.0,
    serve_ui: bool = True,
    open_ui: bool = False,
    keep_ui: bool = True,
    concurrency: int = 6,
    target_names: tuple[str, ...] = DEMO_ARM_TARGETS,
) -> None:
    """Queue demo targets as concurrent tabs in one browser session."""
    store = TakeoverStore(db_path)
    await store.init()
    server_process = (
        start_opensesame_server(opensesame_url=opensesame_url, db_path=db_path)
        if serve_ui
        else None
    )
    if open_ui:
        webbrowser.open(opensesame_url)
    config = browser_config(
        docker_headful=docker_headful,
        port=port,
        docker_version_url=docker_version_url,
    )
    endpoint = f"{opensesame_url.rstrip('/')}/api/voidcrawl/challenge"

    async def arm_one(browser: BrowserSession, name: str, page: Any) -> None:
        url = DEMO_TARGETS[name]
        console.print(f"[bold]arming tab[/] {name}: {url}")
        setup_errors: list[str] = []
        try:
            await page.goto(url, timeout=timeout)
        except Exception as exc:  # live demos should not abort the queue
            setup_errors.append(f"navigate: {exc}")
        try:
            await prepare_demo_target(page, name)
        except Exception as exc:  # XCaptcha prep should be best-effort but visible
            setup_errors.append(f"prepare: {exc}")
        nav_error = "; ".join(setup_errors) if setup_errors else None
        if nav_error:
            console.print(f"[yellow]{name} setup warning:[/] {nav_error}")
        capture = await capture_voidcrawl_challenge(
            browser=browser,
            page=page,
            session_id=f"opensesame-demo-{name}",
            vnc_url=vnc_url,
            novnc_url=novnc_url,
        )
        challenge = capture["challenge"]
        challenge["event_id"] = str(challenge.get("event_id") or uuid4())
        challenge["demo_name"] = name
        if nav_error and not challenge.get("ax_summary"):
            challenge["ax_summary"] = nav_error
        capture["operator_hint"] = (
            capture.get("operator_hint")
            or f"OpenSesame demo capture for {name}; do not auto-solve."
        )
        await asyncio.to_thread(post_json, endpoint, capture)
        console.print(f"[green]queued[/] {name} -> {challenge['event_id']}")

    async def limited_arm_one(
        browser: BrowserSession,
        semaphore: asyncio.Semaphore,
        name: str,
        page: Any,
    ) -> None:
        async with semaphore:
            await arm_one(browser, name, page)

    try:
        async with BrowserSession(config) as browser:
            pages = {
                name: await browser.new_page("about:blank") for name in target_names
            }
            semaphore = asyncio.Semaphore(max(1, concurrency))
            tasks = (
                limited_arm_one(browser, semaphore, name, pages[name])
                for name in target_names
            )
            await asyncio.gather(*tasks)
        if server_process is not None and keep_ui:
            console.print("[green]all demo captures queued[/]; Ctrl-C to stop UI")
            while True:
                await asyncio.sleep(3600)
    finally:
        if server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_process.kill()
