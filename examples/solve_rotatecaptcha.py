#!/usr/bin/env python3
"""Solve the live 2Captcha RotateCaptcha demo with local VoidCrawl.

Rotates the image upright via the widget's arrow controls, verifying against the
demo's own "Check" until it reports the image is upright. Closed-loop, no model,
no per-asset constant.

    PYTHONPATH=src /home/andrew/Desktop/cl/VoidCrawl/.venv/bin/python \
        examples/solve_rotatecaptcha.py --video .local/rotatecaptcha/solve.mp4 --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from open_sesame.harness.page_sync import SerializedPage
from open_sesame.harness.recording import ScreenshotVideoRecorder
from open_sesame.harness.rotate_captcha import read_rotate_state, solve_rotate_captcha

DEMO_URL = "https://2captcha.com/demo/rotatecaptcha"


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
        "extra_args": ["--window-size=1280,900"],
    }
    if args.proxy:
        config_kwargs["proxy"] = args.proxy

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
                await page.wait_for_selector('img[alt="rotatecaptcha example"]', timeout=args.timeout)
            except Exception:
                pass
            await asyncio.sleep(args.settle)
            pre = await read_rotate_state(page)
            on_event(f"loaded RotateCaptcha demo (start angle {pre.angle}deg)")

            result = await solve_rotate_captcha(
                page,
                direction=args.direction,
                max_steps=args.max_steps,
                on_event=on_event,
            )
            payload["result"] = result.as_dict()

            final_shot = work_dir / "rotatecaptcha-final.png"
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
    p.add_argument("--headful", action="store_true")
    p.add_argument("--proxy", default=None)
    p.add_argument("--work-dir", default=".local/rotatecaptcha")
    p.add_argument("--video", default=".local/rotatecaptcha/solve.mp4")
    p.add_argument("--frame-interval", type=float, default=0.4)
    p.add_argument("--direction", choices=["right", "left"], default="right")
    p.add_argument("--max-steps", type=int, default=24)
    p.add_argument("--settle", type=float, default=1.5)
    p.add_argument("--timeout", type=float, default=45.0)
    p.add_argument("--json", action="store_true")
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
        print(f"solved={result.get('solved')} final_angle={result.get('final_angle')}deg steps={result.get('steps')}")
        print(f"video={payload.get('video', {}).get('video_path')}")


if __name__ == "__main__":
    main()
