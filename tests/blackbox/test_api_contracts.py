from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from opensesame.server import create_app


def test_voidcrawl_challenge_api_contract(tmp_path) -> None:
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/voidcrawl/challenge",
            json={
                "operator_hint": "operator action",
                "challenge": {
                    "event_id": "contract-1",
                    "url": "https://example.test",
                    "blocking": True,
                    "dom_captcha": {
                        "kind": "turnstile",
                        "page_url": "https://example.test",
                        "active": True,
                    },
                    "attach_coordinates": {
                        "session_id": "session-1",
                        "handoff_url": "http://127.0.0.1:3069",
                        "remote_browser_url": "http://127.0.0.1:3069",
                    },
                },
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["event"]["event_id"] == "contract-1"
        assert body["event"]["status"] == "pending"
        assert body["event"]["handoff_url"] == "http://127.0.0.1:3069"
        assert body["event"]["remote_browser_url"] == "http://127.0.0.1:3069"
        assert body["event"]["evidence"]["source"] == "voidcrawl.capture_challenge"


def test_voidcrawl_challenge_interrupt_sibling_preserves_event_id(tmp_path) -> None:
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/voidcrawl/challenge",
            json={
                "challenge": {
                    "event_id": "challenge-event-1",
                    "url": "https://example.test",
                },
                "interrupt": {
                    "source": "voidcrawl.capture_challenge",
                    "kind": "captcha",
                    "subkind": "turnstile",
                    "blocking": True,
                    "url": "https://example.test",
                    "attach": {"session_id": "session-1"},
                    "evidence": {"dom_captcha": {"kind": "turnstile"}},
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["event"]["event_id"] == "challenge-event-1"


def test_generic_interrupt_api_contract_and_lifecycle(tmp_path) -> None:
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)
    fixture_path = (
        Path(__file__).parents[2]
        / "docs/fixtures/interrupts/voidcrawl-captcha.json"
    )
    fixture = json.loads(fixture_path.read_text())

    with TestClient(app) as client:
        created = client.post("/api/interrupts", json=fixture)
        assert created.status_code == 200
        assert created.json()["interrupt_id"] == "fixture-voidcrawl-captcha"

        detail = client.get("/api/interrupts/fixture-voidcrawl-captcha")
        assert detail.status_code == 200
        assert detail.json()["event"]["evidence"]["interrupt_kind"] == "captcha"

        denied_fixture = json.loads(
            (
                Path(__file__).parents[2]
                / "docs/fixtures/interrupts/denied-policy.json"
            ).read_text()
        )
        denied = client.post("/api/interrupts", json=denied_fixture)
        assert denied.status_code == 200
        assert denied.json()["event"]["status"] == "denied"

        resolved = client.post(
            "/api/interrupts/fixture-voidcrawl-captcha/resolve",
            json={"resolver": "manual_remote_browser", "note": "cleared"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["event"]["status"] == "resolved"

        failed_race = client.post(
            "/api/interrupts/fixture-voidcrawl-captcha/fail",
            json={"note": "late"},
        )
        assert failed_race.status_code == 200
        assert failed_race.json()["event"]["status"] == "resolved"

        empty_body_fixture = json.loads(json.dumps(fixture))
        empty_body_fixture["interrupt"]["id"] = "fixture-empty-body-resolve"
        created_empty_body = client.post("/api/interrupts", json=empty_body_fixture)
        assert created_empty_body.status_code == 200

        empty_body_resolve = client.post(
            "/api/interrupts/fixture-empty-body-resolve/resolve",
            headers={"content-type": "application/json"},
            content=b"",
        )
        assert empty_body_resolve.status_code == 200
        assert empty_body_resolve.json()["event"]["status"] == "resolved"
        assert empty_body_resolve.json()["event"]["resolver"] == "manual_remote_browser"

        malformed = client.post(
            "/api/interrupts/fixture-empty-body-resolve/fail",
            headers={"content-type": "application/json"},
            content=b"{",
        )
        assert malformed.status_code == 400
        assert malformed.json() == {"detail": "invalid JSON body"}


def test_ok_nonblocking_interrupt_is_terminal_and_quiet(
    tmp_path, monkeypatch
) -> None:
    opened: list[str] = []
    notified: list[tuple[str, str]] = []
    monkeypatch.setattr("opensesame.frontend.app.open_operator", opened.append)
    monkeypatch.setattr(
        "opensesame.frontend.app.notify_takeover",
        lambda title, message: notified.append((title, message)),
    )
    app = create_app(tmp_path / "opensesame.sqlite3", notify=True, open_on_event=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/interrupts",
            json={
                "version": "interrupt.v1",
                "status": "ok",
                "interrupt": {
                    "id": "nonblocking-ok",
                    "source": "qa",
                    "kind": "bot_wall",
                    "blocking": False,
                    "url": "https://example.test/ok",
                    "evidence": {"source": "qa", "data": {}},
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["event"]["status"] == "resolved"
    assert opened == []
    assert notified == []


def test_denied_interrupt_does_not_open_or_notify_operator(
    tmp_path, monkeypatch
) -> None:
    opened: list[str] = []
    notified: list[tuple[str, str]] = []
    monkeypatch.setattr("opensesame.frontend.app.open_operator", opened.append)
    monkeypatch.setattr(
        "opensesame.frontend.app.notify_takeover",
        lambda title, message: notified.append((title, message)),
    )
    app = create_app(tmp_path / "opensesame.sqlite3", notify=True, open_on_event=True)
    fixture = json.loads(
        (
            Path(__file__).parents[2]
            / "docs/fixtures/interrupts/denied-policy.json"
        ).read_text()
    )

    with TestClient(app) as client:
        response = client.post("/api/interrupts", json=fixture)

    assert response.status_code == 200
    assert response.json()["event"]["status"] == "denied"
    assert opened == []
    assert notified == []


def test_takeover_detail_not_found_contract(tmp_path) -> None:
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        response = client.get("/api/takeovers/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "event not found"}
