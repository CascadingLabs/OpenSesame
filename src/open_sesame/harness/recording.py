"""Screenshot → ffmpeg video recorder for live solver demos.

A solve run is only convincing if you can watch it. This records the live page
by polling CDP screenshots on a background task and stitches them into an mp4
with ffmpeg. It shares the page through the caller's serialization lock, so it
never races the solver's own page ops.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
from pathlib import Path
from typing import Any


class ScreenshotVideoRecorder:
    def __init__(
        self,
        page: Any,
        *,
        video_path: Path | None,
        frame_dir: Path | None = None,
        interval: float = 0.5,
    ) -> None:
        self.page = page
        self.video_path = video_path
        self.frame_dir = frame_dir
        self.interval = max(0.2, interval)
        self.frames: list[Path] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self.video_path is None and self.frame_dir is None:
            return
        if self.frame_dir is None:
            assert self.video_path is not None
            self.frame_dir = self.video_path.with_suffix("") / "frames"
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._capture_loop())

    async def stop(self) -> dict[str, object]:
        if self._task is None:
            return {}
        # Cancelling an in-flight CDP call permanently loses a VoidCrawl page, so
        # ask the loop to exit and only cancel as a last resort.
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        encoded = self._encode_video()
        return {
            "frame_dir": str(self.frame_dir) if self.frame_dir else None,
            "frame_count": len(self.frames),
            "video_path": str(encoded) if encoded else None,
        }

    async def _capture_loop(self) -> None:
        assert self.frame_dir is not None
        index = 0
        while not self._stop.is_set():
            frame = self.frame_dir / f"frame_{index:06d}.png"
            try:
                png = await self.page.screenshot_png()
                frame.write_bytes(png)
                self.frames.append(frame)
                index += 1
            except Exception:
                pass
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)

    def _encode_video(self) -> Path | None:
        if self.video_path is None or not self.frames or shutil.which("ffmpeg") is None:
            return None
        self.video_path.parent.mkdir(parents=True, exist_ok=True)
        pattern = str(self.frame_dir / "frame_%06d.png") if self.frame_dir else ""
        command = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(max(1, round(1 / self.interval))),
            "-i",
            pattern,
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-pix_fmt",
            "yuv420p",
            str(self.video_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        return self.video_path if completed.returncode == 0 and self.video_path.exists() else None
