from __future__ import annotations

from fastapi.testclient import TestClient

from opensesame.server import create_app


def test_frontend_shows_declared_interrupt_type(tmp_path) -> None:
    app = create_app(tmp_path / "opensesame.sqlite3", notify=False, open_on_event=False)

    with TestClient(app) as client:
        created = client.post(
            "/api/interrupts",
            json={
                "version": "interrupt.v1",
                "status": "waiting",
                "interrupt": {
                    "id": "auth-interrupt-1",
                    "source": "voidcrawl.interrupt",
                    "kind": "authentication",
                    "subkind": "human_credentials",
                    "blocking": True,
                    "attach": {
                        "session_id": "browser-session-1",
                        "novnc_url": "http://127.0.0.1:6080/vnc.html",
                    },
                    "evidence": {
                        "source": "voidcrawl.interrupt",
                        "data": {"code": "auth.human_required"},
                    },
                },
            },
        )
        assert created.status_code == 200

        events = client.get("/events")

    assert events.status_code == 200
    assert "<h2>authentication</h2>" in events.text
    assert 'id="novnc-frame-auth-interrupt-1"' in events.text
