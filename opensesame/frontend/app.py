from __future__ import annotations

import asyncio
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
        return TEMPLATES.TemplateResponse(request, "index.html", {"events": events})

    @app.get("/events", response_class=HTMLResponse)
    async def events_fragment(request: Request) -> HTMLResponse:
        events = await store.list_events()
        return TEMPLATES.TemplateResponse(request, "events.html", {"events": events})

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
        await announce_takeover(request, takeover.event_id)
        return {"ok": True, "event": takeover.model_dump(mode="json")}

    @app.post("/api/voidcrawl/challenge")
    async def create_voidcrawl_takeover(
        request: Request, payload: VoidCrawlChallengeEnvelope
    ) -> dict[str, object]:
        takeover = await store.create_event(takeover_from_voidcrawl(payload))
        await announce_takeover(request, takeover.event_id)
        return {"ok": True, "event": takeover.model_dump(mode="json")}

    @app.post("/events/{event_id}/resolve")
    async def resolve_takeover(
        event_id: str,
        note: NoteForm = None,
        resolver: ResolverForm = "manual_vnc",
    ) -> RedirectResponse:
        event = await store.resolve_event(event_id, resolver=resolver, note=note)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        return RedirectResponse("/", status_code=303)

    @app.get("/api/takeovers/{event_id}")
    async def get_takeover(event_id: str) -> dict[str, object]:
        event = await store.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        return {"event": event.model_dump(mode="json")}

    @app.get("/events/stream")
    async def event_stream() -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            while True:
                yield "event: ping\ndata: {}\n\n"
                await asyncio.sleep(10)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
