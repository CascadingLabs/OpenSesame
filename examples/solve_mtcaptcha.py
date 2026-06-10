#!/usr/bin/env python3
"""Solve the live 2Captcha MTCaptcha demo with local VoidCrawl + OCR.

Mints a real ``mtcaptcha-verifiedtoken`` in the live session by OCR-reading the
distorted word inside the cross-origin MTCaptcha iframe and typing it in. No
paid solver API, no faked token.

    PYTHONPATH=src /home/andrew/Desktop/cl/VoidCrawl/.venv/bin/python \
        examples/solve_mtcaptcha.py --video .local/mtcaptcha/solve.mp4 --json

The recorder polls page screenshots and stitches an mp4 so the solve is
watchable. ``--ml-python`` points at an interpreter with PIL/numpy for OCR
preprocessing (the VoidCrawl venv has neither); it defaults to ``python3``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from open_sesame.harness.page_sync import SerializedPage
from open_sesame.harness.recording import ScreenshotVideoRecorder
from open_sesame.harness.mtcaptcha import (
    MTCAPTCHA_NO_ISOLATION_ARGS,
    default_scenetext_python,
    read_mt_state,
    solve_mtcaptcha,
)

DEMO_URL = "https://2captcha.com/demo/mtcaptcha"


async def run(args: argparse.Namespace) -> dict[str, object]:
    from voidcrawl import BrowserConfig, BrowserSession

    work_dir = Path(args.work_dir).expanduser()
    work_dir.mkdir(parents=True, exist_ok=True)
    events: list[str] = []

    def on_event(message: str) -> None:
        events.append(message)
        if not args.json:
            print(f"  · {message}", flush=True)

    config_kwargs = {
        "headless": not args.headful,
        "stealth": True,
        # Site isolation off so the cross-origin MTCaptcha widget renders
        # in-process: CDP frame eval reaches it and keystrokes route to its input.
        "extra_args": ["--window-size=1280,900", *MTCAPTCHA_NO_ISOLATION_ARGS],
    }
    if args.proxy:
        config_kwargs["proxy"] = args.proxy
    if args.user_data_dir:
        config_kwargs["user_data_dir"] = args.user_data_dir

    payload: dict[str, object] = {"target_url": DEMO_URL}
    async with BrowserSession(BrowserConfig(**config_kwargs)) as browser:
        page = SerializedPage(await browser.new_page("about:blank"))
        recorder = ScreenshotVideoRecorder(
            page,
            video_path=Path(args.video).expanduser() if args.video else None,
            interval=args.frame_interval,
        )
        await recorder.start()
        try:
            await page.goto(DEMO_URL, timeout=args.timeout, capture_endpoints=False)
            try:
                await page.wait_for_selector('iframe[src*="mtcaptcha"]', timeout=args.timeout)
            except Exception:
                pass
            await asyncio.sleep(args.settle)

            pre = await read_mt_state(page)
            payload["sitekey"] = pre.sitekey
            on_event(f"loaded MTCaptcha demo (sitekey {pre.sitekey})")

            scenetext_python = args.scenetext_python or default_scenetext_python()
            payload["scenetext_python"] = scenetext_python
            result = await solve_mtcaptcha(
                page,
                work_dir=work_dir,
                tesseract_cmd=args.tesseract_cmd,
                ml_python=args.ml_python,
                scenetext_python=scenetext_python,
                max_attempts=args.max_attempts,
                token_wait=args.token_wait,
                on_event=on_event,
            )
            payload["result"] = result.as_dict()

            # Press the demo's own "Check" button so the page confirms the token
            # — the visible green confirmation is the proof in the video.
            if result.solved:
                try:
                    await page.click_by_role("button", "Check")
                    await asyncio.sleep(2.0)
                    payload["demo_checked"] = True
                except Exception as exc:
                    payload["demo_checked"] = f"skipped: {type(exc).__name__}"
            final_shot = work_dir / "mtcaptcha-final.png"
            try:
                await page.screenshot(path=str(final_shot))
                payload["final_screenshot"] = str(final_shot)
            except Exception:
                pass
        finally:
            payload["video"] = await recorder.stop()
            payload["events"] = events
    return payload


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--headful", action="store_true", help="Run a visible browser window.")
    p.add_argument("--proxy", default=None, help="Optional proxy URL.")
    p.add_argument("--user-data-dir", default=None, help="Persistent Chrome profile dir.")
    p.add_argument("--work-dir", default=".local/mtcaptcha", help="Artifacts directory.")
    p.add_argument("--video", default=".local/mtcaptcha/solve.mp4", help="mp4 output path (or empty to skip).")
    p.add_argument("--frame-interval", type=float, default=0.5, help="Seconds between recorded frames.")
    p.add_argument("--max-attempts", type=int, default=5)
    p.add_argument("--token-wait", type=float, default=14.0, help="Seconds to await the token per attempt.")
    p.add_argument("--settle", type=float, default=2.5, help="Dwell after load before solving.")
    p.add_argument("--timeout", type=float, default=45.0)
    p.add_argument("--tesseract-cmd", default="tesseract")
    p.add_argument("--ml-python", default="python3", help="Interpreter with PIL/numpy for OCR preprocessing.")
    p.add_argument(
        "--scenetext-python",
        default=None,
        help="Interpreter with rapidocr-onnxruntime (default: .local/venvs/scenetext if present).",
    )
    p.add_argument("--json", action="store_true", help="Emit only the JSON payload.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.video:
        args.video = None
    payload = asyncio.run(run(args))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        result = payload.get("result", {})
        print()
        print(f"solved={result.get('solved')} token_len={result.get('token_length')}")
        print(f"video={payload.get('video', {}).get('video_path')}")


if __name__ == "__main__":
    main()
