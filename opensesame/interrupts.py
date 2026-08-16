from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from opensesame.events import TakeoverEventCreate, TakeoverStatus

EnvelopeStatus = Literal["ok", "partial", "error", "blocked", "waiting", "denied"]
InterruptLifecycle = Literal["pending", "resolved", "failed", "denied"]


class AttachCoordinates(BaseModel):
    websocket_url: str | None = None
    target_id: str | None = None
    session_id: str | None = None
    handoff_url: str | None = None
    remote_browser_url: str | None = None
    remote_desktop_url: str | None = None
    kasmvnc_url: str | None = None
    vnc_url: str | None = None
    novnc_url: str | None = None

    @model_validator(mode="after")
    def _has_coordinate(self) -> AttachCoordinates:
        if not any(
            (
                self.websocket_url,
                self.target_id,
                self.session_id,
                self.handoff_url,
                self.remote_browser_url,
                self.remote_desktop_url,
                self.kasmvnc_url,
                self.vnc_url,
                self.novnc_url,
            )
        ):
            raise ValueError("attach coordinates must include at least one handle")
        return self


class InterruptEvidence(BaseModel):
    source: str
    data: dict[str, Any] = Field(default_factory=dict)


class ResumeHint(BaseModel):
    strategy: str | None = None
    token: str | None = None
    url: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class Interrupt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    subkind: str | None = None
    lifecycle: InterruptLifecycle = "pending"
    blocking: bool = True
    url: str | None = None
    title: str | None = None
    source: str = "unknown"
    attach: AttachCoordinates | None = None
    evidence: InterruptEvidence | dict[str, Any] = Field(default_factory=dict)
    resume_hint: ResumeHint | dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InterruptEnvelope(BaseModel):
    version: str = "interrupt.v1"
    status: EnvelopeStatus = "blocked"
    interrupt: Interrupt


class ResolutionOutcome(BaseModel):
    id: str
    lifecycle: InterruptLifecycle
    resolver: str | None = None
    note: str | None = None


def takeover_from_interrupt(envelope: InterruptEnvelope) -> TakeoverEventCreate:
    interrupt = envelope.interrupt
    attach = interrupt.attach
    evidence = (
        interrupt.evidence.model_dump(mode="json")
        if isinstance(interrupt.evidence, InterruptEvidence)
        else dict(interrupt.evidence)
    )
    resume_hint = (
        interrupt.resume_hint.model_dump(mode="json")
        if isinstance(interrupt.resume_hint, ResumeHint)
        else interrupt.resume_hint
    )
    evidence.setdefault("source", interrupt.source)
    evidence.update(
        {
            "interrupt_source": interrupt.source,
            "interrupt_version": envelope.version,
            "interrupt_status": envelope.status,
            "interrupt_kind": interrupt.kind,
            "interrupt_subkind": interrupt.subkind,
            "blocking": interrupt.blocking,
            "resume_hint": resume_hint,
            "metadata": interrupt.metadata,
        }
    )
    return TakeoverEventCreate(
        session_id=(
            (attach.session_id if attach else None)
            or interrupt.metadata.get("session_id")
            or "interrupt"
        ),
        event_id=interrupt.id,
        status=_takeover_status(envelope),
        target_id=attach.target_id if attach else None,
        websocket_url=attach.websocket_url if attach else None,
        handoff_url=attach.handoff_url if attach else None,
        remote_browser_url=attach.remote_browser_url if attach else None,
        remote_desktop_url=attach.remote_desktop_url if attach else None,
        kasmvnc_url=attach.kasmvnc_url if attach else None,
        novnc_url=attach.novnc_url if attach else None,
        vnc_url=attach.vnc_url if attach else None,
        url=interrupt.url,
        title=interrupt.title,
        captcha_kind=interrupt.kind if interrupt.kind == "captcha" else None,
        challenge_vendor=interrupt.subkind,
        evidence=evidence,
    )


def _takeover_status(envelope: InterruptEnvelope) -> TakeoverStatus:
    if envelope.status == "denied":
        return "denied"
    lifecycle = envelope.interrupt.lifecycle
    if lifecycle in {"resolved", "failed", "denied"}:
        return lifecycle
    if envelope.status == "ok" or not envelope.interrupt.blocking:
        return "resolved"
    if envelope.status == "error":
        return "failed"
    return "pending"
