from __future__ import annotations

from opensesame.voidcrawl import VoidCrawlChallengeEnvelope, takeover_from_voidcrawl


def test_takeover_from_voidcrawl_capture_payload() -> None:
    payload = VoidCrawlChallengeEnvelope(
        operator_hint="open VNC",
        challenge={
            "event_id": "event-1",
            "url": "https://example.test",
            "blocking": True,
            "status_code": 403,
            "antibot": {"challenge_vendor": "cloudflare"},
            "dom_captcha": {
                "kind": "turnstile",
                "page_url": "https://example.test",
                "active": True,
            },
            "attach_coordinates": {
                "session_id": "session-1",
                "target_id": "target-1",
                "websocket_url": "ws://127.0.0.1/devtools/browser/demo",
                "vnc_url": "vnc://127.0.0.1:5900",
                "novnc_url": "http://127.0.0.1:6080",
            },
        },
    )

    event = takeover_from_voidcrawl(payload)

    assert event.event_id == "event-1"
    assert event.session_id == "session-1"
    assert event.target_id == "target-1"
    assert event.captcha_kind == "turnstile"
    assert event.challenge_vendor == "cloudflare"
    assert event.evidence["source"] == "voidcrawl.capture_challenge"
    assert event.evidence["blocking"] is True
