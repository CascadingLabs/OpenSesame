from __future__ import annotations

from fastapi.testclient import TestClient

from opensesame.server import create_app


def test_frontend_renders_and_voidcrawl_takeover_flow(tmp_path):
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "opensesame-logo.svg" in home.text
        assert "opensesame-logo-light.svg" in home.text
        assert "aria-label=\"Docs\"" in home.text
        assert "title=\"Docs\"" in home.text
        assert "aria-label=\"Notifications\"" in home.text
        assert "id=\"notification-tray\"" in home.text
        assert "data-notification-list" in home.text
        assert "aria-label=\"Queue\"" in home.text
        assert "aria-label=\"History\"" in home.text
        assert 'data-notification-badge' in home.text
        assert 'id="events"' in home.text
        assert 'hx-get="/events"' in home.text
        assert 'opensesame:notifications' in home.text

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

        home_with_notification = client.get("/")
        assert home_with_notification.status_code == 200
        assert 'data-notification-badge' in home_with_notification.text
        assert '>1</span>' in home_with_notification.text

        events = client.get("/events")
        assert events.status_code == 200
        assert "Notifications" not in events.text
        assert "event-1" in events.text
        assert "1 pending" in events.text
        assert (
            "Resolve this challenge to reveal the next pending takeover." in events.text
        )
        assert "noVNC" in events.text
        assert "Native VNC" in events.text
        assert 'hx-preserve="true"' in events.text
        assert 'id="event-details-event-1"' in events.text
        assert 'id="novnc-frame-event-1"' in events.text
        assert 'value="manual_novnc"' in events.text

        history_with_pending = client.get("/history")
        assert history_with_pending.status_code == 200
        assert 'href="/#event-event-1"' in history_with_pending.text

        # Simulate existing rows that recorded noVNC solves as manual_vnc.
        resolved = client.post(
            "/events/event-1/resolve",
            data={"resolver": "manual_vnc", "note": "operator cleared it"},
            follow_redirects=False,
        )
        assert resolved.status_code == 303
        assert resolved.headers["location"] == "/#events"

        detail = client.get("/api/takeovers/event-1")
        assert detail.status_code == 200
        assert detail.json()["event"]["status"] == "resolved"

        history = client.get("/history")
        assert history.status_code == 200
        assert "event-1" in history.text
        assert "manual noVNC" in history.text
        assert "manual_vnc" not in history.text

        empty_events = client.get("/events")
        assert empty_events.status_code == 200
        assert "All solved" in empty_events.text
        assert "No pending takeovers" in empty_events.text


def test_resolving_current_takeover_reveals_next_pending_card(tmp_path):
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    def create_event(client: TestClient, event_id: str) -> None:
        response = client.post(
            "/api/voidcrawl/challenge",
            json={
                "operator_hint": "open VNC",
                "challenge": {
                    "event_id": event_id,
                    "url": f"https://example.test/{event_id}",
                    "blocking": True,
                    "dom_captcha": {
                        "kind": "turnstile",
                        "page_url": f"https://example.test/{event_id}",
                        "active": True,
                    },
                    "attach_coordinates": {
                        "session_id": f"session-{event_id}",
                        "vnc_url": "vnc://127.0.0.1:5900",
                        "novnc_url": f"http://127.0.0.1:6080/{event_id}",
                    },
                },
            },
        )
        assert response.status_code == 200

    with TestClient(app) as client:
        create_event(client, "event-1")
        create_event(client, "event-2")

        events = client.get("/events")
        assert events.status_code == 200
        assert "2 pending" in events.text
        assert "event-2" in events.text
        assert "event-1" not in events.text

        resolved = client.post(
            "/events/event-2/resolve",
            data={"resolver": "manual_novnc", "note": "operator cleared it"},
            follow_redirects=False,
        )
        assert resolved.status_code == 303
        assert resolved.headers["location"] == "/#event-event-1"

        next_events = client.get("/events")
        assert next_events.status_code == 200
        assert "1 pending" in next_events.text
        assert "event-1" in next_events.text
        assert "event-2" not in next_events.text
