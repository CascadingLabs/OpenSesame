"""Live-demo support: a serialized page proxy + a screenshot→mp4 recorder.

A solve is only convincing if you can watch it, so these examples record the live
page to an mp4. VoidCrawl pages are single-flight (one in-flight CDP call at a
time), so the background screenshot loop and the solver's own page ops must share
one lock — hence ``SerializedPage``. The recorder polls CDP screenshots and
stitches them with ffmpeg.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import shutil
import subprocess
from pathlib import Path
from typing import Any


class SerializedPage:
    """Route every page call through one lock so concurrent helpers can't poison it."""

    def __init__(self, page: Any) -> None:
        self._page = page
        self._lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._page, name)
        if not callable(attr):
            return attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            async with self._lock:
                result = attr(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result

        return call


class ScreenshotRecorder:
    def __init__(self, page: Any, video_path: Path | None, *, interval: float = 0.5) -> None:
        self.page = page
        self.video_path = video_path
        self.frame_dir = video_path.with_suffix("") / "frames" if video_path else None
        self.interval = max(0.2, interval)
        self.frames: list[Path] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self.frame_dir is None:
            return
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> dict[str, object]:
        if self._task is None:
            return {}
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        encoded = self._encode()
        return {"video_path": str(encoded) if encoded else None, "frames": len(self.frames)}

    async def _loop(self) -> None:
        assert self.frame_dir is not None
        index = 0
        while not self._stop.is_set():
            try:
                png = await self.page.screenshot_png()
                (self.frame_dir / f"frame_{index:06d}.png").write_bytes(png)
                self.frames.append(self.frame_dir / f"frame_{index:06d}.png")
                index += 1
            except Exception:
                pass
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)

    def _encode(self) -> Path | None:
        if self.video_path is None or not self.frames or shutil.which("ffmpeg") is None:
            return None
        self.video_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-framerate", str(max(1, round(1 / self.interval))),
            "-i", str(self.frame_dir / "frame_%06d.png"),
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p", str(self.video_path),
        ]
        done = subprocess.run(cmd, capture_output=True, text=True)
        return self.video_path if done.returncode == 0 and self.video_path.exists() else None
