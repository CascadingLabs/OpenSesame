from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from opensesame.events import TakeoverEventCreate


class VoidCrawlChallengeEnvelope(BaseModel):
    challenge: dict[str, Any] = Field(default_factory=dict)
    operator_hint: str | None = None


def takeover_from_voidcrawl(
    payload: VoidCrawlChallengeEnvelope,
    *,
    fallback_session_id: str = "voidcrawl",
) -> TakeoverEventCreate:
    challenge = payload.challenge
    attach = _dict(challenge.get("attach_coordinates"))
    antibot = _dict(challenge.get("antibot"))
    dom_captcha = _dict(challenge.get("dom_captcha"))

    return TakeoverEventCreate(
        session_id=str(attach.get("session_id") or fallback_session_id),
        event_id=str(challenge.get("event_id")),
        target_id=_str_or_none(attach.get("target_id")),
        websocket_url=_str_or_none(attach.get("websocket_url")),
        novnc_url=_str_or_none(attach.get("novnc_url")),
        vnc_url=_str_or_none(attach.get("vnc_url")),
        url=_str_or_none(challenge.get("url") or dom_captcha.get("page_url")),
        captcha_kind=_str_or_none(dom_captcha.get("kind")),
        challenge_vendor=_str_or_none(antibot.get("challenge_vendor")),
        evidence={
            "source": "voidcrawl.capture_challenge",
            "blocking": challenge.get("blocking"),
            "status_code": challenge.get("status_code"),
            "antibot": antibot,
            "dom_captcha": dom_captcha,
            "operator_hint": payload.operator_hint,
            "ax_summary": challenge.get("ax_summary"),
        },
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
