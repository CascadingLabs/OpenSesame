from __future__ import annotations

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
                    "attach_coordinates": {"session_id": "session-1"},
                },
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["event"]["event_id"] == "contract-1"
        assert body["event"]["status"] == "pending"
        assert body["event"]["evidence"]["source"] == "voidcrawl.capture_challenge"


def test_takeover_detail_not_found_contract(tmp_path) -> None:
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        response = client.get("/api/takeovers/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "event not found"}
