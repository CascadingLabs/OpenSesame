from __future__ import annotations

import sqlite3

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
            handoff_url="http://127.0.0.1:3069",
            remote_browser_url="http://127.0.0.1:3069",
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
    assert resolved.resolver == "manual_remote_browser"
    assert resolved.note == "operator cleared it"
    assert await store.count_events(status="pending") == 0
    assert await store.count_events(exclude_status="pending") == 1


@pytest.mark.asyncio
async def test_init_migrates_existing_db_for_handoff_urls(tmp_path):
    db_path = tmp_path / "opensesame.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE takeover_events (
              event_id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              status TEXT NOT NULL,
              target_id TEXT,
              websocket_url TEXT,
              novnc_url TEXT,
              vnc_url TEXT,
              url TEXT,
              title TEXT,
              captcha_kind TEXT,
              challenge_vendor TEXT,
              evidence_json TEXT NOT NULL DEFAULT '{}',
              resolver TEXT,
              note TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )

    store = TakeoverStore(db_path)
    await store.init()
    created = await store.create_event(
        TakeoverEventCreate(
            session_id="session-1",
            event_id="event-1",
            handoff_url="http://127.0.0.1:3069",
            remote_browser_url="http://127.0.0.1:3069",
        )
    )

    assert created.handoff_url == "http://127.0.0.1:3069"
    assert created.remote_browser_url == "http://127.0.0.1:3069"


@pytest.mark.asyncio
async def test_duplicate_create_updates_pending_but_not_terminal_event(tmp_path):
    store = TakeoverStore(tmp_path / "opensesame.sqlite3")
    await store.init()

    await store.create_event(
        TakeoverEventCreate(
            session_id="session-1",
            event_id="event-1",
            evidence={"version": 1},
        )
    )
    updated = await store.create_event(
        TakeoverEventCreate(
            session_id="session-2",
            event_id="event-1",
            handoff_url="http://127.0.0.1:3069",
            evidence={"version": 2},
        )
    )

    assert updated.session_id == "session-2"
    assert updated.handoff_url == "http://127.0.0.1:3069"
    assert updated.evidence == {"version": 2}

    resolved = await store.resolve_event("event-1", note="done")
    assert resolved is not None
    stale_retry = await store.create_event(
        TakeoverEventCreate(
            session_id="session-3",
            event_id="event-1",
            status="pending",
            handoff_url="http://127.0.0.1:9999",
            evidence={"version": 3},
        )
    )

    assert stale_retry.status == "resolved"
    assert stale_retry.session_id == "session-2"
    assert stale_retry.handoff_url == "http://127.0.0.1:3069"
    assert stale_retry.note == "done"
    assert stale_retry.evidence == {"version": 2}


@pytest.mark.asyncio
async def test_bulk_resolve_only_updates_pending_events(tmp_path):
    store = TakeoverStore(tmp_path / "opensesame.sqlite3")
    await store.init()

    for event_id in ("event-1", "event-2", "event-3"):
        await store.create_event(
            TakeoverEventCreate(
                session_id=f"session-{event_id}",
                event_id=event_id,
                handoff_url=f"http://127.0.0.1:3069/{event_id}",
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
    assert event_2.resolver == "manual_remote_browser"
    assert event_2.note == "batch cleared"
    assert event_3 is not None
    assert event_3.status == "pending"

    first_page = await store.list_events(exclude_status="pending", limit=1, offset=0)
    second_page = await store.list_events(exclude_status="pending", limit=1, offset=1)
    assert len(first_page) == 1
    assert len(second_page) == 1
