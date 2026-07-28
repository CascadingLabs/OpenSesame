"""Caller-owned bridge from VoidCrawl interrupts to the OpenSesame operator UI.

CAS-253 intentionally has no cross-process resolver.  The process that owns a
``BrowserSession`` therefore creates the interrupt, waits for the local UI's
terminal state, then resumes or releases that same session itself.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal
from urllib import error, request

from pydantic import BaseModel, Field, model_validator

from opensesame.interrupts import InterruptEnvelope

InterruptKind = Literal["captcha", "manual", "sensitive_action", "authentication"]


class VoidCrawlHandoffConfig(BaseModel, frozen=True):
    """Non-secret coordinates for an OpenSesame operator handoff."""

    opensesame_url: str = Field(default="http://127.0.0.1:8765", min_length=1)
    session_id: str = Field(min_length=1)
    handoff_url: str | None = None
    remote_browser_url: str | None = None
    remote_desktop_url: str | None = None
    kasmvnc_url: str | None = None
    novnc_url: str | None = None
    vnc_url: str | None = None
    poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    resolution_timeout_seconds: float = Field(default=600.0, gt=0, le=3600)
    request_timeout_seconds: float = Field(default=15.0, gt=0, le=60)

    @model_validator(mode="after")
    def _has_operator_viewer(self) -> VoidCrawlHandoffConfig:
        if not any(
            (
                self.handoff_url,
                self.remote_browser_url,
                self.remote_desktop_url,
                self.kasmvnc_url,
                self.novnc_url,
                self.vnc_url,
            )
        ):
            raise ValueError("configure at least one operator viewer URL")
        return self


class VoidCrawlHandoffResult(BaseModel, frozen=True):
    """Terminal state applied by the owning VoidCrawl session."""

    interrupt_id: str
    operator_status: Literal["resolved", "failed", "denied"]
    voidcrawl_state: Literal["resumed", "released"]
    resolver: str | None = None
    note: str | None = None


class OpenSesameHandoffError(RuntimeError):
    """OpenSesame could not safely relay or resolve an interrupt."""


async def _json_request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    def send() -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={"content-type": "application/json"},
            method=method,
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
                decoded = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenSesameHandoffError(
                f"{method} {url} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise OpenSesameHandoffError(
                f"{method} {url} failed: {exc.reason}"
            ) from exc
        if not isinstance(decoded, dict):
            raise OpenSesameHandoffError(
                f"{method} {url} returned a non-object JSON body"
            )
        return decoded

    return await asyncio.to_thread(send)


class VoidCrawlInterruptHandoff:
    """Relay an explicit VoidCrawl interrupt and apply the UI's outcome.

    This object deliberately owns no browser connection.  Give it the live
    session and page from the caller that detected the condition; that process
    remains responsible for resuming or releasing the retained target.
    """

    def __init__(self, config: VoidCrawlHandoffConfig) -> None:
        self.config = config

    async def interrupt_and_wait(
        self,
        browser: Any,
        page: Any,
        interrupt_request: Any,
        *,
        kind: InterruptKind,
        subkind: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VoidCrawlHandoffResult:
        """Park *page*, send a redacted event, then resume or release it.

        ``interrupt_request`` is a ``voidcrawl.InterruptRequest`` at runtime.
        It is intentionally untyped here so importing OpenSesame does not
        require a CAS-253 build until a caller uses this integration.
        """
        interrupt = await browser.interrupt(page, interrupt_request)
        interrupt_id = str(interrupt.interrupt_id)
        try:
            envelope = await self._envelope(
                page,
                interrupt,
                kind=kind,
                subkind=subkind,
                title=title,
                metadata=metadata,
            )
            created = await _json_request(
                "POST",
                self._endpoint("/api/interrupts"),
                payload=envelope.model_dump(mode="json", exclude_none=True),
                timeout_seconds=self.config.request_timeout_seconds,
            )
            if created.get("interrupt_id") != interrupt_id:
                raise OpenSesameHandoffError(
                    "OpenSesame returned a different interrupt ID"
                )
            event = await self._wait_for_terminal_event(
                interrupt_id,
                timeout_seconds=min(
                    self.config.resolution_timeout_seconds,
                    int(interrupt.expires_in_ms) / 1000,
                ),
            )
            status = event.get("status")
            if status == "resolved":
                result = await browser.resume(interrupt_id)
                if result.state != "resumed":
                    raise OpenSesameHandoffError(
                        "VoidCrawl did not resume the interrupt"
                    )
                operator_status: Literal["resolved", "failed", "denied"] = "resolved"
                voidcrawl_state: Literal["resumed", "released"] = "resumed"
            elif status == "failed":
                result = await browser.release(interrupt_id)
                if result.state != "released":
                    raise OpenSesameHandoffError(
                        "VoidCrawl did not release the interrupt"
                    )
                operator_status = "failed"
                voidcrawl_state = "released"
            elif status == "denied":
                result = await browser.release(interrupt_id)
                if result.state != "released":
                    raise OpenSesameHandoffError(
                        "VoidCrawl did not release the interrupt"
                    )
                operator_status = "denied"
                voidcrawl_state = "released"
            else:
                raise OpenSesameHandoffError(
                    f"OpenSesame returned unsupported terminal status: {status!r}"
                )
            return VoidCrawlHandoffResult(
                interrupt_id=interrupt_id,
                operator_status=operator_status,
                voidcrawl_state=voidcrawl_state,
                resolver=_optional_str(event.get("resolver")),
                note=_optional_str(event.get("note")),
            )
        except BaseException:
            # A failed relay, timeout, or caller cancellation must not leave a
            # retained browser target parked until its TTL by accident.
            await browser.release(interrupt_id)
            raise

    async def _envelope(
        self,
        page: Any,
        interrupt: Any,
        *,
        kind: InterruptKind,
        subkind: str | None,
        title: str | None,
        metadata: dict[str, Any] | None,
    ) -> InterruptEnvelope:
        page_url = await page.url()
        return InterruptEnvelope.model_validate(
            {
                "version": "interrupt.v1",
                "status": "waiting",
                "interrupt": {
                    "id": str(interrupt.interrupt_id),
                    "source": "voidcrawl.interrupt",
                    "kind": kind,
                    "subkind": subkind,
                    "blocking": True,
                    "url": str(page_url) if page_url else None,
                    "title": title,
                    "attach": {
                        "session_id": self.config.session_id,
                        "handoff_url": self.config.handoff_url,
                        "remote_browser_url": self.config.remote_browser_url,
                        "remote_desktop_url": self.config.remote_desktop_url,
                        "kasmvnc_url": self.config.kasmvnc_url,
                        "novnc_url": self.config.novnc_url,
                        "vnc_url": self.config.vnc_url,
                    },
                    "evidence": {
                        "source": "voidcrawl.interrupt",
                        "data": {
                            "code": str(interrupt.code),
                            "summary": str(interrupt.summary),
                            "expires_in_ms": int(interrupt.expires_in_ms),
                        },
                    },
                    "metadata": metadata or {},
                },
            }
        )

    async def _wait_for_terminal_event(
        self, interrupt_id: str, *, timeout_seconds: float
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        endpoint = self._endpoint(f"/api/interrupts/{interrupt_id}")
        while time.monotonic() < deadline:
            response = await _json_request(
                "GET",
                endpoint,
                payload=None,
                timeout_seconds=self.config.request_timeout_seconds,
            )
            event = response.get("event")
            if not isinstance(event, dict):
                raise OpenSesameHandoffError(
                    "OpenSesame response did not contain an event"
                )
            if event.get("status") != "pending":
                return event
            await asyncio.sleep(self.config.poll_interval_seconds)
        raise TimeoutError(
            f"OpenSesame did not resolve interrupt {interrupt_id} before expiry"
        )

    def _endpoint(self, path: str) -> str:
        return f"{self.config.opensesame_url.rstrip('/')}{path}"


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
