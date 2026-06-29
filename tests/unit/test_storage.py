from __future__ import annotations

import pytest

from opensesame.events import TakeoverEventCreate
from opensesame.storage import TakeoverStore


@pytest.mark.asyncio
async def test_takeover_event_lifecycle(tmp_path):
    store = TakeoverStore(tmp_path / "opensesame.sqlite3")
    await store.init()

    created = await store.create_event(
        TakeoverEventCreate(
            session_id="session-1",
            event_id="event-1",
            vnc_url="vnc://127.0.0.1:5900",
            novnc_url="http://127.0.0.1:6080",
            captcha_kind="turnstile",
            evidence={"source": "test"},
        )
    )

    assert created.status == "pending"
    assert created.evidence == {"source": "test"}

    pending = await store.list_events(status="pending")
    assert [event.event_id for event in pending] == ["event-1"]

    resolved = await store.resolve_event("event-1", note="operator cleared it")
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.resolver == "manual_novnc"
    assert resolved.note == "operator cleared it"
