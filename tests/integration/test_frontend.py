from __future__ import annotations

from fastapi.testclient import TestClient

from opensesame.server import create_app


def test_frontend_renders_and_voidcrawl_takeover_flow(tmp_path):
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        assert client.get("/").status_code == 200

        created = client.post(
            "/api/voidcrawl/challenge",
            json={
                "operator_hint": "open VNC",
                "challenge": {
                    "event_id": "event-1",
                    "url": "https://example.test",
                    "blocking": True,
                    "dom_captcha": {
                        "kind": "turnstile",
                        "page_url": "https://example.test",
                        "active": True,
                    },
                    "attach_coordinates": {
                        "session_id": "session-1",
                        "vnc_url": "vnc://127.0.0.1:5900",
                        "novnc_url": "http://127.0.0.1:6080",
                    },
                },
            },
        )
        assert created.status_code == 200

        events = client.get("/events")
        assert events.status_code == 200
        assert "event-1" in events.text
        assert "Open VNC" in events.text

        resolved = client.post(
            "/events/event-1/resolve",
            data={"resolver": "manual_vnc", "note": "operator cleared it"},
            follow_redirects=False,
        )
        assert resolved.status_code == 303

        detail = client.get("/api/takeovers/event-1")
        assert detail.status_code == 200
        assert detail.json()["event"]["status"] == "resolved"
