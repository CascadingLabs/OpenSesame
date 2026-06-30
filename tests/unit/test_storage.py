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

    assert await store.count_events(status="pending") == 1
    assert await store.count_events(exclude_status="pending") == 0

    resolved = await store.resolve_event("event-1", note="operator cleared it")
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.resolver == "manual_novnc"
    assert resolved.note == "operator cleared it"
    assert await store.count_events(status="pending") == 0
    assert await store.count_events(exclude_status="pending") == 1


@pytest.mark.asyncio
async def test_bulk_resolve_only_updates_pending_events(tmp_path):
    store = TakeoverStore(tmp_path / "opensesame.sqlite3")
    await store.init()

    for event_id in ("event-1", "event-2", "event-3"):
        await store.create_event(
            TakeoverEventCreate(
                session_id=f"session-{event_id}",
                event_id=event_id,
                novnc_url=f"http://127.0.0.1:6080/{event_id}",
                captcha_kind="turnstile",
            )
        )

    await store.resolve_event("event-1", note="already done")
    resolved = await store.resolve_events(
        ["event-1", "event-2", "missing", "event-2"], note="batch cleared"
    )

    assert [event.event_id for event in resolved] == ["event-2"]
    event_1 = await store.get_event("event-1")
    event_2 = await store.get_event("event-2")
    event_3 = await store.get_event("event-3")
    assert event_1 is not None
    assert event_1.note == "already done"
    assert event_2 is not None
    assert event_2.status == "resolved"
    assert event_2.resolver == "manual_novnc"
    assert event_2.note == "batch cleared"
    assert event_3 is not None
    assert event_3.status == "pending"

    first_page = await store.list_events(exclude_status="pending", limit=1, offset=0)
    second_page = await store.list_events(exclude_status="pending", limit=1, offset=1)
    assert len(first_page) == 1
    assert len(second_page) == 1
