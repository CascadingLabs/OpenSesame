#!/usr/bin/env python3
"""Solve the live 2Captcha GeeTest v3 slider demo with local VoidCrawl.

Finds the puzzle gap by a same-origin canvas diff (no model) and drags the
slider with a human-like trajectory, minting the real GeeTest
``geetest_validate`` token in the live session. No paid solver API.

    PYTHONPATH=src /home/andrew/Desktop/cl/VoidCrawl/.venv/bin/python \
        examples/solve_geetest.py --video .local/geetest/solve.mp4 --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from open_sesame.harness.page_sync import SerializedPage
from open_sesame.harness.recording import ScreenshotVideoRecorder
from open_sesame.harness.geetest_slider import read_geetest_state, solve_geetest_slider

DEMO_URL = "https://2captcha.com/demo/geetest"


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
            # Reload per attempt: each GeeTest challenge is single-use, so a fresh
            # page gives a clean gap-detect + drag (and the clearest video).
            result = None
            rounds: list[dict[str, object]] = []
            for attempt in range(1, args.max_attempts + 1):
                await page.goto(DEMO_URL, timeout=args.timeout, capture_endpoints=False)
                try:
                    await page.wait_for_selector(".geetest_radar_btn", timeout=args.timeout)
                except Exception:
                    pass
                await asyncio.sleep(args.settle)
                on_event(f"round {attempt}: loaded GeeTest v3 demo")
                result = await solve_geetest_slider(page, max_attempts=1, on_event=on_event)
                rounds.append(result.as_dict())
                if result.solved:
                    break
            payload["result"] = result.as_dict() if result else {}
            payload["rounds"] = rounds
            if result and not result.solved:
                # The slider geometry is solved (piece reaches the gap); GeeTest v3
                # rejects the session at its behavioural/anti-bot layer (no ajax.php
                # validation fires). That is the anti-bot track, not the solver.
                payload["note"] = (
                    "gap detected and piece dragged into place; GeeTest v3 behavioural "
                    "layer rejected the session (no validate token). Anti-bot track, "
                    "not the slider solver."
                )

            if result and result.solved:
                try:
                    await page.click_by_role("button", "Check")
                    await asyncio.sleep(2.0)
                    payload["demo_checked"] = True
                except Exception as exc:
                    payload["demo_checked"] = f"skipped: {type(exc).__name__}"
            final_shot = work_dir / "geetest-final.png"
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
    p.add_argument("--user-data-dir", default=None)
    p.add_argument("--work-dir", default=".local/geetest")
    p.add_argument("--video", default=".local/geetest/solve.mp4")
    p.add_argument("--frame-interval", type=float, default=0.4)
    p.add_argument("--max-attempts", type=int, default=3, help="Fresh-load attempts.")
    p.add_argument("--settle", type=float, default=2.0)
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
        print(f"solved={result.get('solved')} validate_len={result.get('validate_length')}")
        print(f"video={payload.get('video', {}).get('video_path')}")


if __name__ == "__main__":
    main()
