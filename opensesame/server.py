from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from opensesame.frontend import FrontendSettings
from opensesame.frontend import create_app as create_frontend_app
from opensesame.storage import DEFAULT_DB_PATH


def create_app(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    public_url: str = "http://127.0.0.1:8765",
    notify: bool = True,
    open_on_event: bool = True,
) -> FastAPI:
    """Compatibility wrapper for the packaged OpenSesame frontend."""
    return create_frontend_app(
        FrontendSettings(
            db_path=db_path,
            public_url=public_url,
            notify=notify,
            open_on_event=open_on_event,
        )
    )
