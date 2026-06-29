from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

TakeoverStatus = Literal["pending", "resolved", "failed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TakeoverEventCreate(BaseModel):
    session_id: str
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    target_id: str | None = None
    websocket_url: str | None = None
    novnc_url: str | None = None
    vnc_url: str | None = None
    url: str | None = None
    title: str | None = None
    captcha_kind: str | None = None
    challenge_vendor: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class TakeoverEvent(TakeoverEventCreate):
    status: TakeoverStatus = "pending"
    resolver: str | None = None
    note: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
