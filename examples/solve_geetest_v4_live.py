#!/usr/bin/env python3
"""Live GeeTest v4 slider solve through the OpenSesame public API.

Drives 2Captcha's GeeTest v4 demo (https://2captcha.com/demo/geetest-v4). v4 opens
a popup whose background + puzzle piece are CSS background-image PNGs from
static.geetest.com; OpenSesame fetches them, finds the notch by edge-matching the
piece silhouette (no model), waits for the popup to settle, and drags the piece
there with a human-like trajectory. Success is GeeTest's "you beat N% of users"
state. The behavioural layer is intermittent on a headless session, so this
reloads for a fresh challenge each attempt.

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_geetest_v4_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from OpenSesame import Challenge, Family, SolverPolicy
from OpenSesame.api.defaults import default_solver

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recording import ScreenshotRecorder, SerializedPage  # noqa: E402

DEMO = "https://2captcha.com/demo/geetest-v4"
VIDEO = Path(".local/videos/geetest_v4.mp4")
ATTEMPTS = 8


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    policy = SolverPolicy.auto_only(allow_sites=["2captcha.com"], auto_timeout_s=45.0)
    solver = default_solver(policy)
    challenge = Challenge(family=Family.GEETEST, url=DEMO, host="2captcha.com")

    async with BrowserSession(BrowserConfig(
        headless=True, stealth=True,
        # Wide enough that the popup's full slider travel stays on-screen.
        extra_args=["--window-size=1500,1000"],
    )) as browser:
        page = SerializedPage(await browser.new_page("about:blank"))
        recorder = ScreenshotRecorder(page, VIDEO, interval=0.4)
        await recorder.start()
        result = None
        try:
            for attempt in range(1, ATTEMPTS + 1):
                await page.goto(DEMO, timeout=45)
                try:
                    await page.wait_for_selector(".geetest_btn_click", timeout=30)
                except Exception:
                    pass
                await asyncio.sleep(2.0)
                result = await solver.solve(challenge, page=page, timeout=50)
                meta = result.metadata
                print(f"  attempt {attempt}: status={result.status.value} "
                      f"drag={meta.get('drag_css')} result={meta.get('result')!r}")
                if result.ok:
                    break
        finally:
            video = await recorder.stop()

    if result and result.ok:
        print(f"✓ PASSED — GeeTest v4 slide solved ({result.metadata.get('result')})")
    else:
        print("✗ not solved across attempts — gap or behavioural reject "
              f"(last: {result.metadata.get('result') if result else 'n/a'})")
    print(f"  video: {video.get('video_path')}")
    return 0 if (result and result.ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
