from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opensesame import cli, demo


def test_open_url_prompt_loop_allows_repeated_opens(monkeypatch):
    url = "http://127.0.0.1:8765"
    answers = iter(["o", " O ", "no", "", "o"])
    opened: list[str] = []

    def fake_input(prompt: str) -> str:
        try:
            return next(answers)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr(cli.console, "input", fake_input)
    monkeypatch.setattr(cli.webbrowser, "open", lambda value: opened.append(value))

    cli._open_url_prompt_loop(url)

    assert opened == [url, url, url]


def test_demo_server_spawn_disables_hidden_open_prompt(monkeypatch):
    readiness = iter([False, True])
    spawned: dict[str, object] = {}

    class FakeProcess:
        def poll(self) -> None:
            return None

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        spawned["command"] = command
        spawned["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(demo, "url_is_ready", lambda _url: next(readiness))
    monkeypatch.setattr(demo.subprocess, "Popen", fake_popen)

    process = demo.start_opensesame_server(
        opensesame_url="http://127.0.0.1:8765",
        db_path=Path("test.sqlite3"),
        notify=False,
    )

    assert process is not None
    assert "--no-open-prompt" in spawned["command"]
    assert spawned["kwargs"]["stdin"] is subprocess.DEVNULL


@pytest.mark.asyncio
async def test_demo_uses_voidcrawl_capture_challenge_contract():
    class FakeBrowser:
        async def websocket_url(self) -> str:
            return "ws://127.0.0.1/devtools/browser/demo"

    class FakePage:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        async def capture_challenge(self, **kwargs: object) -> dict[str, object]:
            self.kwargs = kwargs
            return {"challenge": {"event_id": "event-1", "blocking": True}}

    page = FakePage()

    capture = await demo.capture_voidcrawl_challenge(
        browser=FakeBrowser(),
        page=page,
        session_id="opensesame-demo",
        vnc_url="vnc://127.0.0.1:5900",
        novnc_url="http://127.0.0.1:6080",
    )

    assert capture["challenge"]["event_id"] == "event-1"
    assert page.kwargs == {
        "websocket_url": "ws://127.0.0.1/devtools/browser/demo",
        "session_id": "opensesame-demo",
        "vnc_url": "vnc://127.0.0.1:5900",
        "novnc_url": "http://127.0.0.1:6080",
    }
