from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from opensesame.events import TakeoverEventCreate
from opensesame.notify import notify_takeover, open_operator
from opensesame.storage import DEFAULT_DB_PATH, TakeoverStore
from opensesame.voidcrawl import VoidCrawlChallengeEnvelope, takeover_from_voidcrawl

PACKAGE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
NoteForm = Annotated[str | None, Form()]
ResolverForm = Annotated[str, Form()]


@dataclass(frozen=True)
class FrontendSettings:
    db_path: Path | str = DEFAULT_DB_PATH
    public_url: str = "http://127.0.0.1:8765"
    notify: bool = True
    open_on_event: bool = True


def create_app(settings: FrontendSettings | None = None) -> FastAPI:
    resolved = settings or FrontendSettings()
    store = TakeoverStore(resolved.db_path)
    sse_clients: set[asyncio.Queue[dict[str, object]]] = set()

    async def notification_payload(
        kind: str, event_id: str | None = None
    ) -> dict[str, object]:
        events = await store.list_events()
        pending = [event for event in events if event.status == "pending"]
        payload: dict[str, object] = {
            "kind": kind,
            "pending_count": len(pending),
            "pending": [
                {
                    "event_id": event.event_id,
                    "title": (
                        event.captcha_kind or event.challenge_vendor or "challenge"
                    ),
                }
                for event in pending
            ],
        }
        if event_id is not None:
            payload["event_id"] = event_id
        return payload

    def sse_event(event: str, data: dict[str, object]) -> str:
        encoded = json.dumps(data, separators=(",", ":"))
        return f"event: {event}\ndata: {encoded}\n\n"

    async def broadcast_notifications(
        kind: str, event_id: str | None = None
    ) -> None:
        payload = await notification_payload(kind, event_id)
        for client in tuple(sse_clients):
            client.put_nowait(payload)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await store.init()
        app.state.store = store
        app.state.public_url = resolved.public_url
        app.state.notify = resolved.notify
        app.state.open_on_event = resolved.open_on_event
        yield

    app = FastAPI(title="OpenSesame", lifespan=lifespan)
    app.mount(
        "/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static"
    )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        events = await store.list_events()
        pending = [event for event in events if event.status == "pending"]
        resolved = [event for event in events if event.status != "pending"]
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"events": events, "pending": pending, "resolved_count": len(resolved)},
        )

    @app.get("/events", response_class=HTMLResponse)
    async def events_fragment(request: Request) -> HTMLResponse:
        events = await store.list_events()
        pending = [event for event in events if event.status == "pending"]
        resolved = [event for event in events if event.status != "pending"]
        return TEMPLATES.TemplateResponse(
            request,
            "events.html",
            {"events": events, "pending": pending, "resolved_count": len(resolved)},
        )

    @app.get("/history", response_class=HTMLResponse)
    async def history(request: Request) -> HTMLResponse:
        events = await store.list_events()
        resolved = [event for event in events if event.status != "pending"]
        pending_count = sum(1 for event in events if event.status == "pending")
        pending = [event for event in events if event.status == "pending"]
        return TEMPLATES.TemplateResponse(
            request,
            "history.html",
            {"resolved": resolved, "pending": pending, "pending_count": pending_count},
        )

    async def announce_takeover(request: Request, event_id: str) -> None:
        url = f"{request.app.state.public_url}/#event-{event_id}"
        if request.app.state.notify:
            notify_takeover("OpenSesame takeover", f"Challenge queued: {event_id}")
        if request.app.state.open_on_event:
            open_operator(url)

    @app.post("/api/takeovers")
    async def create_takeover(
        request: Request, event: TakeoverEventCreate
    ) -> dict[str, object]:
        takeover = await store.create_event(event)
        await broadcast_notifications("created", takeover.event_id)
        await announce_takeover(request, takeover.event_id)
        return {"ok": True, "event": takeover.model_dump(mode="json")}

    @app.post("/api/voidcrawl/challenge")
    async def create_voidcrawl_takeover(
        request: Request, payload: VoidCrawlChallengeEnvelope
    ) -> dict[str, object]:
        takeover = await store.create_event(takeover_from_voidcrawl(payload))
        await broadcast_notifications("created", takeover.event_id)
        await announce_takeover(request, takeover.event_id)
        return {"ok": True, "event": takeover.model_dump(mode="json")}

    @app.get("/events/{event_id}/novnc", response_class=HTMLResponse)
    async def novnc_viewer(event_id: str, request: Request) -> HTMLResponse:
        event = await store.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        if not event.novnc_url:
            raise HTTPException(status_code=404, detail="no noVNC URL for event")
        return TEMPLATES.TemplateResponse(request, "novnc.html", {"event": event})

    @app.post("/events/{event_id}/resolve")
    async def resolve_takeover(
        event_id: str,
        note: NoteForm = None,
        resolver: ResolverForm = "manual_novnc",
    ) -> RedirectResponse:
        event = await store.resolve_event(event_id, resolver=resolver, note=note)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        await broadcast_notifications("resolved", event_id)
        next_pending = await store.list_events("pending")
        target = f"/#event-{next_pending[0].event_id}" if next_pending else "/#events"
        return RedirectResponse(target, status_code=303)

    @app.get("/api/takeovers/{event_id}")
    async def get_takeover(event_id: str) -> dict[str, object]:
        event = await store.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        return {"event": event.model_dump(mode="json")}

    @app.get("/events/stream")
    async def event_stream(request: Request) -> StreamingResponse:
        client: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        sse_clients.add(client)

        async def stream() -> AsyncIterator[str]:
            try:
                yield sse_event("notifications", await notification_payload("snapshot"))
                while not await request.is_disconnected():
                    try:
                        payload = await asyncio.wait_for(client.get(), timeout=10)
                    except TimeoutError:
                        yield sse_event("ping", {})
                    else:
                        yield sse_event("notifications", payload)
            finally:
                sse_clients.discard(client)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
