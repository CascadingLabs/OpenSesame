from __future__ import annotations

from typing import Any

import pytest

from opensesame.voidcrawl_interrupts import (
    VoidCrawlHandoffConfig,
    VoidCrawlHandoffResult,
    bind_opensesame,
)


class FakeBrowser:
    def __init__(self) -> None:
        self.opened: list[str] = []

    async def new_page(self, url: str) -> str:
        self.opened.append(url)
        return f"page:{url}"


def handoff_config(**overrides: Any) -> VoidCrawlHandoffConfig:
    settings: dict[str, Any] = {
        "session_id": "browser-session-1",
        "novnc_url": "http://127.0.0.1:6080/vnc.html",
    }
    settings.update(overrides)
    return VoidCrawlHandoffConfig(**settings)


@pytest.mark.asyncio
async def test_binding_exposes_the_declared_handoff_config() -> None:
    browser = FakeBrowser()
    config = handoff_config()

    async with bind_opensesame(browser, config) as session:
        assert session.browser is browser
        assert session.config == config
        assert session.handoff.config == config


@pytest.mark.asyncio
async def test_new_page_opens_on_the_bound_session() -> None:
    browser = FakeBrowser()

    async with bind_opensesame(browser, handoff_config()) as session:
        page = await session.new_page("https://example.test/login")

    assert page == "page:https://example.test/login"
    assert browser.opened == ["https://example.test/login"]


@pytest.mark.asyncio
async def test_require_human_relays_declared_interrupt_metadata() -> None:
    browser = FakeBrowser()
    captured: dict[str, Any] = {}
    outcome = VoidCrawlHandoffResult(
        interrupt_id="interrupt-1",
        operator_status="resolved",
        voidcrawl_state="resumed",
    )

    async def fake_interrupt_and_wait(
        browser_arg: Any,
        page: Any,
        interrupt_request: Any,
        **declared: Any,
    ) -> VoidCrawlHandoffResult:
        captured["browser"] = browser_arg
        captured["page"] = page
        captured["request"] = interrupt_request
        captured["declared"] = declared
        return outcome

    async with bind_opensesame(browser, handoff_config()) as session:
        session.handoff.interrupt_and_wait = fake_interrupt_and_wait  # type: ignore[method-assign]
        result = await session.require_human(
            "page:login",
            code="captcha.human_required",
            summary="Solve the local fixture challenge and sign in.",
            kind="captcha",
            subkind="fixture_visual",
            title="Local captcha login fixture",
            ttl_seconds=120,
        )

    assert result is outcome
    assert captured["browser"] is browser
    assert captured["page"] == "page:login"
    assert captured["request"].code == "captcha.human_required"
    assert captured["request"].ttl_seconds == 120
    assert captured["declared"] == {
        "kind": "captcha",
        "subkind": "fixture_visual",
        "title": "Local captcha login fixture",
        "metadata": None,
    }


@pytest.mark.asyncio
async def test_require_human_ttl_defaults_to_configured_timeout() -> None:
    browser = FakeBrowser()
    captured: dict[str, Any] = {}

    async def fake_interrupt_and_wait(
        browser_arg: Any, page: Any, interrupt_request: Any, **declared: Any
    ) -> VoidCrawlHandoffResult:
        captured["request"] = interrupt_request
        return VoidCrawlHandoffResult(
            interrupt_id="interrupt-1",
            operator_status="resolved",
            voidcrawl_state="resumed",
        )

    config = handoff_config(resolution_timeout_seconds=42)
    async with bind_opensesame(browser, config) as session:
        session.handoff.interrupt_and_wait = fake_interrupt_and_wait  # type: ignore[method-assign]
        await session.require_human(
            "page:login",
            code="captcha.human_required",
            summary="Solve the local fixture challenge.",
            kind="captcha",
        )

    assert captured["request"].ttl_seconds == 42
