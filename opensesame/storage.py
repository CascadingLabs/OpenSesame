from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from opensesame.events import TakeoverEvent, TakeoverEventCreate, utc_now

DEFAULT_DB_PATH = Path(".opensesame/opensesame.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS takeover_events (
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
);
CREATE INDEX IF NOT EXISTS idx_takeover_events_status_created
  ON takeover_events(status, created_at DESC);
"""


class TakeoverStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            await db.executescript(SCHEMA)
            await db.commit()

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    async def create_event(self, event: TakeoverEventCreate) -> TakeoverEvent:
        now = utc_now()
        takeover = TakeoverEvent(**event.model_dump(), created_at=now, updated_at=now)
        async with self.connect() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO takeover_events (
                  event_id, session_id, status, target_id, websocket_url, novnc_url,
                  vnc_url, url, title, captcha_kind, challenge_vendor, evidence_json,
                  resolver, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _to_row_values(takeover),
            )
            await db.commit()
        return takeover

    async def list_events(self, status: str | None = None) -> list[TakeoverEvent]:
        where = "WHERE status = ?" if status else ""
        params: tuple[Any, ...] = (status,) if status else ()
        async with self.connect() as db:
            cursor = await db.execute(
                f"SELECT * FROM takeover_events {where} ORDER BY created_at DESC",
                params,
            )
            rows = await cursor.fetchall()
        return [_from_row(row) for row in rows]

    async def get_event(self, event_id: str) -> TakeoverEvent | None:
        async with self.connect() as db:
            cursor = await db.execute(
                "SELECT * FROM takeover_events WHERE event_id = ?",
                (event_id,),
            )
            row = await cursor.fetchone()
        return _from_row(row) if row else None

    async def resolve_event(
        self,
        event_id: str,
        *,
        resolver: str = "manual_vnc",
        note: str | None = None,
        status: str = "resolved",
    ) -> TakeoverEvent | None:
        updated_at = utc_now().isoformat()
        async with self.connect() as db:
            await db.execute(
                """
                UPDATE takeover_events
                SET status = ?, resolver = ?, note = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (status, resolver, note, updated_at, event_id),
            )
            await db.commit()
        return await self.get_event(event_id)


def _to_row_values(event: TakeoverEvent) -> tuple[Any, ...]:
    return (
        event.event_id,
        event.session_id,
        event.status,
        event.target_id,
        event.websocket_url,
        event.novnc_url,
        event.vnc_url,
        event.url,
        event.title,
        event.captcha_kind,
        event.challenge_vendor,
        json.dumps(event.evidence, sort_keys=True),
        event.resolver,
        event.note,
        event.created_at.isoformat(),
        event.updated_at.isoformat(),
    )


def _from_row(row: aiosqlite.Row) -> TakeoverEvent:
    data = dict(row)
    data["evidence"] = json.loads(data.pop("evidence_json") or "{}")
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    data["updated_at"] = datetime.fromisoformat(data["updated_at"])
    return TakeoverEvent(**data)
