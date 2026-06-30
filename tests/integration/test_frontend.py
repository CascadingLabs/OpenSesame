from __future__ import annotations

from fastapi.testclient import TestClient

from opensesame.server import create_app


def create_voidcrawl_event(client: TestClient, event_id: str) -> None:
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


def test_frontend_renders_and_voidcrawl_takeover_flow(tmp_path):
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "opensesame-logo.svg" in home.text
        assert "opensesame-logo-light.svg" in home.text
        assert 'aria-label="Docs"' in home.text
        assert 'title="Docs"' in home.text
        assert 'aria-label="Notifications"' in home.text
        assert 'id="notification-tray"' in home.text
        assert "data-notification-list" in home.text
        assert 'aria-label="Workbench"' in home.text
        assert 'aria-label="Queue"' in home.text
        assert 'aria-label="History"' in home.text
        assert "data-notification-badge" in home.text
        assert 'id="events"' in home.text
        assert 'hx-get="/events"' in home.text
        assert "opensesame:notifications" in home.text

        create_voidcrawl_event(client, "event-1")

        home_with_notification = client.get("/")
        assert home_with_notification.status_code == 200
        assert "data-notification-badge" in home_with_notification.text
        assert ">1</span>" in home_with_notification.text

        events = client.get("/events")
        assert events.status_code == 200
        assert "Notifications" not in events.text
        assert "event-1" in events.text
        assert "1 pending" in events.text
        assert "Solve the active noVNC session" in events.text
        assert "Pending queue" not in events.text
        assert "Mark selected resolved" not in events.text
        assert 'name="event_ids"' not in events.text
        assert "noVNC" in events.text
        assert "Native VNC" in events.text
        assert 'hx-preserve="true"' in events.text
        assert 'id="event-details-event-1"' in events.text
        assert 'id="novnc-frame-event-1"' in events.text
        assert 'value="manual_novnc"' in events.text

        queue = client.get("/queue")
        assert queue.status_code == 200
        assert "Open active workbench" not in queue.text
        assert "Manage pending queue" in queue.text
        assert "Mark selected resolved" in queue.text
        assert 'name="event_ids"' in queue.text
        assert "Created" in queue.text
        assert "Updated" in queue.text
        assert "Workbench" in queue.text
        assert "Native VNC" in queue.text
        assert "event-1" in queue.text

        grouped_queue = client.get("/queue?sort=oldest&group=session")
        assert grouped_queue.status_code == 200
        assert "session-event-1" in grouped_queue.text
        assert "Oldest first" in grouped_queue.text

        history_with_pending = client.get("/history")
        assert history_with_pending.status_code == 200
        assert 'href="/queue#event-event-1"' in history_with_pending.text

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
        assert '<table class="event-table history-table">' in history.text
        assert "event-1" in history.text
        assert "manual noVNC" in history.text
        assert "manual_vnc" not in history.text
        assert "Page 1 / 1" in history.text

        empty_events = client.get("/events")
        assert empty_events.status_code == 200
        assert "All solved" in empty_events.text
        assert "No pending takeovers" in empty_events.text


def test_notification_tray_renders_all_items_for_css_scrolling(tmp_path):
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        for index in range(8):
            create_voidcrawl_event(client, f"event-{index}")

        home = client.get("/")
        assert home.status_code == 200
        assert ">8</span>" in home.text
        assert home.text.count('data-target-id="event-') == 8
        assert home.text.count('href="/queue#event-') == 8


def test_resolving_current_takeover_reveals_next_pending_card(tmp_path):
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        create_voidcrawl_event(client, "event-1")
        create_voidcrawl_event(client, "event-2")

        events = client.get("/events")
        assert events.status_code == 200
        assert "2 pending" in events.text
        assert "event-2" in events.text
        assert "event-1" not in events.text
        assert "Manage queue" in events.text

        queue = client.get("/queue")
        assert queue.status_code == 200
        assert "Pending queue" in queue.text
        assert "event-2" in queue.text
        assert "event-1" in queue.text

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


def test_bulk_resolve_selected_pending_events_and_paginate_history(tmp_path):
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        create_voidcrawl_event(client, "event-1")
        create_voidcrawl_event(client, "event-2")
        create_voidcrawl_event(client, "event-3")

        resolved = client.post(
            "/events/resolve",
            data={
                "event_ids": ["event-3", "event-2"],
                "resolver": "manual_novnc",
                "note": "batch cleared",
            },
            follow_redirects=False,
        )
        assert resolved.status_code == 303
        assert resolved.headers["location"] == "/#event-event-1"

        event_1 = client.get("/api/takeovers/event-1").json()["event"]
        event_2 = client.get("/api/takeovers/event-2").json()["event"]
        event_3 = client.get("/api/takeovers/event-3").json()["event"]
        assert event_1["status"] == "pending"
        assert event_2["status"] == "resolved"
        assert event_2["resolver"] == "manual_novnc"
        assert event_2["note"] == "batch cleared"
        assert event_3["status"] == "resolved"

        history = client.get("/history?per_page=1")
        assert history.status_code == 200
        assert "Page 1 / 2" in history.text
        assert "Next →" in history.text
        assert "manual noVNC" in history.text

        second_page = client.get("/history?page=2&per_page=1")
        assert second_page.status_code == 200
        assert "Page 2 / 2" in second_page.text
        assert "← Previous" in second_page.text
