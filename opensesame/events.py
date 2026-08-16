from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

TakeoverStatus = Literal["pending", "resolved", "failed", "denied"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TakeoverEventCreate(BaseModel):
    session_id: str
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    status: TakeoverStatus = "pending"
    target_id: str | None = None
    websocket_url: str | None = None
    handoff_url: str | None = None
    remote_browser_url: str | None = None
    remote_desktop_url: str | None = None
    kasmvnc_url: str | None = None
    novnc_url: str | None = None
    vnc_url: str | None = None
    url: str | None = None
    title: str | None = None
    captcha_kind: str | None = None
    challenge_vendor: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class TakeoverEvent(TakeoverEventCreate):
    resolver: str | None = None
    note: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def display_kind(self) -> str:
        interrupt_kind = self.evidence.get("interrupt_kind")
        if isinstance(interrupt_kind, str) and interrupt_kind:
            return interrupt_kind
        return self.captcha_kind or self.challenge_vendor or "challenge"

    @property
    def operator_url(self) -> str | None:
        return (
            self.handoff_url
            or self.remote_browser_url
            or self.remote_desktop_url
            or self.kasmvnc_url
            or self.novnc_url
        )

    @property
    def operator_label(self) -> str:
        if self.handoff_url or self.remote_browser_url:
            return "Remote browser"
        if self.remote_desktop_url:
            return "Remote desktop"
        if self.kasmvnc_url:
            return "KasmVNC"
        if self.novnc_url:
            return "noVNC"
        if self.vnc_url:
            return "Native VNC"
        return "Remote browser"

    @property
    def manual_resolver(self) -> str:
        if self.handoff_url or self.remote_browser_url or self.remote_desktop_url:
            return "manual_remote_browser"
        if self.kasmvnc_url:
            return "manual_kasmvnc"
        if self.novnc_url:
            return "manual_novnc"
        if self.vnc_url:
            return "manual_vnc"
        return "manual_remote_browser"
