from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from fastapi.testclient import TestClient

from opensesame.server import create_app


def _load_example(name: str) -> ModuleType:
    path = Path(__file__).parents[2] / f"examples/{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_login_example_posts_generic_interrupt_contract(tmp_path) -> None:
    example = _load_example("live_login_handoff")
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)
    payload = example.build_login_interrupt(
        event_id="login-example-contract",
        url="https://the-internet.herokuapp.com/login",
        websocket_url="ws://127.0.0.1:9222/devtools/browser/example",
        target_id="target-login",
        yosoi_version="0.0.3a21",
        voidcrawl_version="0.3.8.2",
        handoff_url="http://127.0.0.1:3069",
        remote_browser_url="http://127.0.0.1:3069",
        kasmvnc_url=None,
        novnc_url=None,
        vnc_url=None,
    )

    with TestClient(app) as client:
        response = client.post("/api/interrupts", json=payload)

    assert response.status_code == 200
    event = response.json()["event"]
    assert event["event_id"] == "login-example-contract"
    assert event["status"] == "pending"
    assert event["target_id"] == "target-login"
    assert event["handoff_url"] == "http://127.0.0.1:3069"
    assert event["remote_browser_url"] == "http://127.0.0.1:3069"
    assert event["kasmvnc_url"] is None
    assert event["novnc_url"] is None
    assert event["evidence"]["interrupt_kind"] == "login_required"
    assert event["evidence"]["data"]["secrets_stored_by_example"] is False


def test_oauth_example_posts_generic_interrupt_contract(tmp_path) -> None:
    example = _load_example("oauth_handoff")
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)
    payload = example.build_oauth_interrupt(
        event_id="oauth-example-contract",
        authorization_url=(
            "https://accounts.example.test/oauth/authorize?"
            "client_id=demo&response_type=code&scope=read&state=abc"
        ),
        provider="example",
        session_id="oauth-session",
    )

    with TestClient(app) as client:
        response = client.post("/api/interrupts", json=payload)

    assert response.status_code == 200
    event = response.json()["event"]
    assert event["event_id"] == "oauth-example-contract"
    assert event["status"] == "pending"
    assert event["session_id"] == "oauth-session"
    assert event["url"] == "https://accounts.example.test/oauth/authorize"
    assert event["evidence"]["source"] == "oauth.authorization_required"
    assert event["evidence"]["interrupt_source"] == "opensesame.examples.oauth_handoff"
    assert event["evidence"]["interrupt_kind"] == "oauth"
    assert event["evidence"]["data"]["query_redacted"] is True
    assert event["evidence"]["data"]["client_secret_stored"] is False
