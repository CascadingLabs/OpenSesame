from __future__ import annotations

from types import SimpleNamespace

import pytest

from opensesame.voidcrawl_interrupts import (
    OpenSesameHandoffError,
    VoidCrawlHandoffConfig,
    VoidCrawlInterruptHandoff,
)


class FakePage:
    async def url(self) -> str:
        return "https://example.test/sign-in"


class FakeBrowser:
    def __init__(self) -> None:
        self.interrupt_calls: list[tuple[object, object]] = []
        self.resume_calls: list[str] = []
        self.release_calls: list[str] = []

    async def interrupt(self, page: object, request: object) -> SimpleNamespace:
        self.interrupt_calls.append((page, request))
        return SimpleNamespace(
            interrupt_id="interrupt-1",
            code="auth.human_required",
            summary="Human sign-in required",
            expires_in_ms=600_000,
            state="interrupted",
        )

    async def resume(self, interrupt_id: str) -> SimpleNamespace:
        self.resume_calls.append(interrupt_id)
        return SimpleNamespace(state="resumed")

    async def release(self, interrupt_id: str) -> SimpleNamespace:
        self.release_calls.append(interrupt_id)
        return SimpleNamespace(state="released")


def configured_handoff() -> VoidCrawlInterruptHandoff:
    return VoidCrawlInterruptHandoff(
        VoidCrawlHandoffConfig(
            session_id="browser-session-1",
            novnc_url="http://127.0.0.1:6080/vnc.html",
            poll_interval_seconds=0.001,
        )
    )


@pytest.mark.asyncio
async def test_handoff_resumes_owning_voidcrawl_session_after_operator_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    responses = iter(
        [
            {"ok": True, "interrupt_id": "interrupt-1"},
            {"event": {"status": "pending"}},
            {
                "event": {
                    "status": "resolved",
                    "resolver": "manual_novnc",
                    "note": "operator completed sign-in",
                }
            },
        ]
    )

    async def fake_json_request(
        method: str,
        url: str,
        *,
        payload: dict[str, object] | None,
        timeout_seconds: float,
    ) -> dict[str, object]:
        calls.append((method, url, payload))
        return next(responses)

    monkeypatch.setattr(
        "opensesame.voidcrawl_interrupts._json_request", fake_json_request
    )
    browser = FakeBrowser()
    page = FakePage()

    result = await configured_handoff().interrupt_and_wait(
        browser,
        page,
        object(),
        kind="authentication",
        subkind="human_credentials",
    )

    assert result.operator_status == "resolved"
    assert result.voidcrawl_state == "resumed"
    assert result.resolver == "manual_novnc"
    assert browser.resume_calls == ["interrupt-1"]
    assert browser.release_calls == []
    assert [call[0] for call in calls] == ["POST", "GET", "GET"]
    payload = calls[0][2]
    assert payload is not None
    interrupt = payload["interrupt"]
    assert isinstance(interrupt, dict)
    assert interrupt["id"] == "interrupt-1"
    assert interrupt["kind"] == "authentication"
    assert interrupt["attach"] == {
        "session_id": "browser-session-1",
        "novnc_url": "http://127.0.0.1:6080/vnc.html",
    }
    assert "websocket_url" not in interrupt["attach"]
    assert "target_id" not in interrupt["attach"]


@pytest.mark.asyncio
async def test_handoff_releases_session_when_operator_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            {"ok": True, "interrupt_id": "interrupt-1"},
            {"event": {"status": "denied", "note": "policy denied"}},
        ]
    )

    async def fake_json_request(
        method: str,
        url: str,
        *,
        payload: dict[str, object] | None,
        timeout_seconds: float,
    ) -> dict[str, object]:
        return next(responses)

    monkeypatch.setattr(
        "opensesame.voidcrawl_interrupts._json_request", fake_json_request
    )
    browser = FakeBrowser()

    result = await configured_handoff().interrupt_and_wait(
        browser, FakePage(), object(), kind="sensitive_action"
    )

    assert result.operator_status == "denied"
    assert result.voidcrawl_state == "released"
    assert browser.resume_calls == []
    assert browser.release_calls == ["interrupt-1"]


@pytest.mark.asyncio
async def test_handoff_releases_session_when_ui_relay_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_json_request(
        method: str,
        url: str,
        *,
        payload: dict[str, object] | None,
        timeout_seconds: float,
    ) -> dict[str, object]:
        raise OpenSesameHandoffError("OpenSesame is unavailable")

    monkeypatch.setattr(
        "opensesame.voidcrawl_interrupts._json_request", failing_json_request
    )
    browser = FakeBrowser()

    with pytest.raises(OpenSesameHandoffError, match="unavailable"):
        await configured_handoff().interrupt_and_wait(
            browser, FakePage(), object(), kind="manual"
        )

    assert browser.resume_calls == []
    assert browser.release_calls == ["interrupt-1"]


@pytest.mark.asyncio
async def test_handoff_never_waits_past_voidcrawl_interrupt_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_json_request(
        method: str,
        url: str,
        *,
        payload: dict[str, object] | None,
        timeout_seconds: float,
    ) -> dict[str, object]:
        return {"ok": True, "interrupt_id": "interrupt-1"}

    monkeypatch.setattr(
        "opensesame.voidcrawl_interrupts._json_request", fake_json_request
    )
    browser = FakeBrowser()

    async def short_lived_interrupt(page: object, request: object) -> SimpleNamespace:
        return SimpleNamespace(
            interrupt_id="interrupt-1",
            code="captcha.human_required",
            summary="Human CAPTCHA completion required",
            expires_in_ms=1_000,
            state="interrupted",
        )

    browser.interrupt = short_lived_interrupt  # type: ignore[method-assign]
    handoff = configured_handoff()
    observed_timeout: float | None = None

    async def resolved_before_expiry(
        interrupt_id: str, *, timeout_seconds: float
    ) -> dict[str, object]:
        nonlocal observed_timeout
        observed_timeout = timeout_seconds
        return {"status": "resolved"}

    monkeypatch.setattr(handoff, "_wait_for_terminal_event", resolved_before_expiry)

    result = await handoff.interrupt_and_wait(
        browser, FakePage(), object(), kind="captcha"
    )

    assert result.voidcrawl_state == "resumed"
    assert observed_timeout == 1.0
