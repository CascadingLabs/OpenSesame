from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

import rich_click as click
from granian import Granian
from granian.constants import Interfaces
from rich.console import Console
from rich.table import Table

from opensesame.demo import (
    DEFAULT_DOCKER_CDP_VERSION_URL,
    DEFAULT_NOVNC_URL,
    DEFAULT_OPENSESAME_URL,
    DEFAULT_VNC_URL,
    DEMO_TARGETS,
    run_demo,
)
from opensesame.events import TakeoverEvent
from opensesame.storage import DEFAULT_DB_PATH, TakeoverStore

click.rich_click.TEXT_MARKUP = "rich"
_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "show_default": True}

console = Console()


def run_asgi_app(
    *,
    db_path: Path,
    public_url: str,
    notify: bool,
    open_on_event: bool,
    host: str,
    port: int,
) -> None:
    os.environ["OPENSESAME_DB_PATH"] = str(db_path)
    os.environ["OPENSESAME_PUBLIC_URL"] = public_url
    os.environ["OPENSESAME_NOTIFY"] = "1" if notify else "0"
    os.environ["OPENSESAME_OPEN_ON_EVENT"] = "1" if open_on_event else "0"
    server = Granian(
        "opensesame.server:create_app_from_env",
        address=host,
        port=port,
        interface=Interfaces.ASGI,
        factory=True,
    )
    server.serve()


def package_version() -> str:
    return importlib.metadata.version("opensesame")


def echo_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, sort_keys=True))


def wants_json(ctx: click.Context) -> bool:
    return bool(ctx.obj and ctx.obj.get("json"))


def _open_url_prompt_loop(url: str) -> None:
    while True:
        try:
            answer = console.input("Press [bold]o[/]+Enter to open OpenSesame: ")
        except (EOFError, KeyboardInterrupt):
            return
        if answer.strip().lower() == "o":
            webbrowser.open(url)


def prompt_open_url(url: str) -> None:
    if not sys.stdin.isatty():
        return

    threading.Thread(target=_open_url_prompt_loop, args=(url,), daemon=True).start()


@click.group(context_settings=_CONTEXT_SETTINGS)
@click.option("--json", "json_output", "-j", is_flag=True, help="Emit JSON output.")
@click.version_option(package_version(), "--version", "-v", prog_name="opensesame")
@click.pass_context
def main(ctx: click.Context, json_output: bool) -> None:
    """OpenSesame human takeover control center."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8765, type=int)
@click.option("--public-url", default=None, help="URL used in notifications.")
@click.option("--notify/--no-notify", default=True)
@click.option("--open-on-event/--no-open-on-event", default=True)
@click.option("--open-prompt/--no-open-prompt", default=True)
@click.option(
    "--db",
    "db_path",
    default=str(DEFAULT_DB_PATH),
    type=click.Path(path_type=Path),
)
@click.pass_context
def serve(
    ctx: click.Context,
    host: str,
    port: int,
    public_url: str | None,
    notify: bool,
    open_on_event: bool,
    open_prompt: bool,
    db_path: Path,
) -> None:
    """Serve the FastAPI/HTMX operator UI."""
    url = public_url or f"http://{host}:{port}"
    if wants_json(ctx):
        echo_json({"event": "serve_start", "url": url, "db": str(db_path)})
    else:
        console.print(f"[bold green]OpenSesame[/] serving {url}")
        console.print(f"storage: [cyan]{db_path}[/]")
        if open_prompt:
            prompt_open_url(url)
    run_asgi_app(
        db_path=db_path,
        public_url=url,
        notify=notify,
        open_on_event=open_on_event,
        host=host,
        port=port,
    )


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8765, type=int)
@click.option(
    "--db",
    "db_path",
    default=str(DEFAULT_DB_PATH),
    type=click.Path(path_type=Path),
)
@click.pass_context
def watch(ctx: click.Context, host: str, port: int, db_path: Path) -> None:
    """Open the operator UI in a browser, then serve it."""
    url = f"http://{host}:{port}"
    if wants_json(ctx):
        echo_json({"event": "watch_start", "url": url, "db": str(db_path)})
    else:
        console.print(f"opening [link={url}]{url}[/link]")
    webbrowser.open(url)
    run_asgi_app(
        db_path=db_path,
        public_url=url,
        notify=True,
        open_on_event=True,
        host=host,
        port=port,
    )


@main.group()
def demo() -> None:
    """Drive local VoidCrawl challenge takeover demos."""


@demo.command("run")
@click.argument("challenge_type", type=click.Choice(sorted(DEMO_TARGETS)))
@click.option("--url", default=None, help="Override the built-in demo URL.")
@click.option("--opensesame-url", default=DEFAULT_OPENSESAME_URL)
@click.option("--port", default=9222, type=int)
@click.option(
    "--docker-headful",
    is_flag=True,
    help="Attach to docker/run-headful.sh Chrome.",
)
@click.option("--docker-version-url", default=DEFAULT_DOCKER_CDP_VERSION_URL)
@click.option("--novnc-url", default=DEFAULT_NOVNC_URL)
@click.option("--vnc-url", default=DEFAULT_VNC_URL)
@click.option("--timeout", default=15.0, type=float, help="Navigation timeout seconds.")
@click.option(
    "--serve-ui/--no-serve-ui",
    default=True,
    help="Start OpenSesame if needed.",
)
@click.option("--open-ui", is_flag=True, help="Open OpenSesame immediately.")
@click.option(
    "--ui-prompt/--no-ui-prompt",
    default=True,
    help="Prompt: press o+Enter to open OpenSesame.",
)
@click.option(
    "--db",
    "db_path",
    default=str(DEFAULT_DB_PATH),
    type=click.Path(path_type=Path),
)
@click.pass_context
def demo_run(
    ctx: click.Context,
    challenge_type: str,
    url: str | None,
    opensesame_url: str,
    port: int,
    docker_headful: bool,
    docker_version_url: str,
    novnc_url: str,
    vnc_url: str,
    timeout: float,
    serve_ui: bool,
    open_ui: bool,
    ui_prompt: bool,
    db_path: Path,
) -> None:
    """Launch VoidCrawl, send interrupt to OpenSesame, wait for resolution."""
    target_url = url or DEMO_TARGETS[challenge_type]
    if wants_json(ctx):
        echo_json(
            {
                "event": "demo_start",
                "type": challenge_type,
                "url": target_url,
                "opensesame_url": opensesame_url,
                "db": str(db_path),
                "docker_headful": docker_headful,
                "timeout": timeout,
                "serve_ui": serve_ui,
                "open_ui": open_ui,
                "ui_prompt": ui_prompt,
            }
        )
    asyncio.run(
        run_demo(
            db_path=db_path,
            challenge_type=challenge_type,
            url=url,
            opensesame_url=opensesame_url,
            port=port,
            docker_headful=docker_headful,
            docker_version_url=docker_version_url,
            novnc_url=novnc_url,
            vnc_url=vnc_url,
            timeout=timeout,
            serve_ui=serve_ui,
            open_ui=open_ui,
            ui_prompt=ui_prompt,
        )
    )


def demo_options(fn: Any) -> Any:
    fn = click.option("--url", default=None, help="Override the built-in demo URL.")(fn)
    fn = click.option("--opensesame-url", default=DEFAULT_OPENSESAME_URL)(fn)
    fn = click.option("--port", default=9222, type=int)(fn)
    fn = click.option(
        "--docker-headful/--local-headful",
        default=True,
        help="Attach to docker/run-headful.sh Chrome or launch local Chrome.",
    )(fn)
    fn = click.option(
        "--docker-version-url", default=DEFAULT_DOCKER_CDP_VERSION_URL
    )(fn)
    fn = click.option("--novnc-url", default=DEFAULT_NOVNC_URL)(fn)
    fn = click.option("--vnc-url", default=DEFAULT_VNC_URL)(fn)
    fn = click.option("--timeout", default=15.0, type=float)(fn)
    fn = click.option("--serve-ui/--no-serve-ui", default=True)(fn)
    fn = click.option(
        "--open-ui", is_flag=True, help="Open OpenSesame immediately."
    )(fn)
    fn = click.option(
        "--ui-prompt/--no-ui-prompt",
        default=True,
        help="Prompt: press o+Enter to open OpenSesame.",
    )(fn)
    fn = click.option(
        "--db",
        "db_path",
        default=str(DEFAULT_DB_PATH),
        type=click.Path(path_type=Path),
    )(fn)
    return fn


def invoke_demo(
    ctx: click.Context,
    challenge_type: str,
    url: str | None,
    opensesame_url: str,
    port: int,
    docker_headful: bool,
    docker_version_url: str,
    novnc_url: str,
    vnc_url: str,
    timeout: float,
    serve_ui: bool,
    open_ui: bool,
    ui_prompt: bool,
    db_path: Path,
) -> None:
    ctx.invoke(
        demo_run,
        challenge_type=challenge_type,
        url=url,
        opensesame_url=opensesame_url,
        port=port,
        docker_headful=docker_headful,
        docker_version_url=docker_version_url,
        novnc_url=novnc_url,
        vnc_url=vnc_url,
        timeout=timeout,
        serve_ui=serve_ui,
        open_ui=open_ui,
        ui_prompt=ui_prompt,
        db_path=db_path,
    )


@demo.command("cloudflare")
@demo_options
@click.pass_context
def demo_cloudflare(ctx: click.Context, /, **kwargs: Any) -> None:
    """Run the Cloudflare Turnstile takeover demo."""
    invoke_demo(ctx, "cloudflare", **kwargs)


@demo.command("turnstile")
@demo_options
@click.pass_context
def demo_turnstile(ctx: click.Context, /, **kwargs: Any) -> None:
    """Run the Cloudflare Turnstile takeover demo."""
    invoke_demo(ctx, "turnstile", **kwargs)


@demo.command("recaptcha")
@demo_options
@click.pass_context
def demo_recaptcha(ctx: click.Context, /, **kwargs: Any) -> None:
    """Run the reCAPTCHA v2 takeover demo."""
    invoke_demo(ctx, "recaptcha", **kwargs)


@main.command("list")
@click.option(
    "--db",
    "db_path",
    default=str(DEFAULT_DB_PATH),
    type=click.Path(path_type=Path),
)
@click.option("--status", default=None, help="Filter by pending/resolved/failed.")
@click.pass_context
def list_events(ctx: click.Context, db_path: Path, status: str | None) -> None:
    """List takeover events from local SQLite storage."""
    events = asyncio.run(_load_events(db_path, status))
    if wants_json(ctx):
        echo_json({"events": [event.model_dump(mode="json") for event in events]})
        return

    table = Table(title="OpenSesame takeover events")
    table.add_column("Status")
    table.add_column("Event")
    table.add_column("Session")
    table.add_column("Challenge")
    table.add_column("URL")
    for event in events:
        table.add_row(
            event.status,
            event.event_id,
            event.session_id,
            event.captcha_kind or event.challenge_vendor or "-",
            event.url or "-",
        )
    console.print(table)


async def _load_events(db_path: Path, status: str | None) -> list[TakeoverEvent]:
    store = TakeoverStore(db_path)
    await store.init()
    return await store.list_events(status=status)
