#!/usr/bin/env python3
"""Live RotateCaptcha solve through the OpenSesame public API.

Drives 2Captcha's rotate demo (https://2captcha.com/demo/rotatecaptcha). The
answer is a rotation: OpenSesame rotates the image with the widget's arrow
controls and verifies against the demo's own "Check" oracle, settling on the
centre of the accepted window so the image ends genuinely upright. No model.

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_rotatecaptcha_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from OpenSesame import Challenge, Family, SolverPolicy
from OpenSesame.api.defaults import default_solver

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recording import ScreenshotRecorder, SerializedPage  # noqa: E402

DEMO = "https://2captcha.com/demo/rotatecaptcha"
VIDEO = Path(".local/videos/rotatecaptcha.mp4")


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    policy = SolverPolicy.auto_only(allow_sites=["2captcha.com"], auto_timeout_s=90.0)
    solver = default_solver(policy)
    challenge = Challenge(family=Family.ROTATE, url=DEMO, host="2captcha.com")

    async with BrowserSession(BrowserConfig(
        headless=True, stealth=True, extra_args=["--window-size=1280,900"],
    )) as browser:
        page = SerializedPage(await browser.new_page("about:blank"))
        recorder = ScreenshotRecorder(page, VIDEO, interval=0.4)
        await recorder.start()
        try:
            await page.goto(DEMO, timeout=45)
            try:
                await page.wait_for_selector('img[alt="rotatecaptcha example"]', timeout=30)
            except Exception:
                pass
            await asyncio.sleep(1.5)
            result = await solver.solve(challenge, page=page, timeout=100)
            await asyncio.sleep(1.5)  # let the success banner sit on video
        finally:
            video = await recorder.stop()

    if result.ok:
        print(f"✓ PASSED — image upright at {result.answer}deg "
              f"(accepted window {result.metadata.get('accepted_window_deg')})")
    else:
        print(f"✗ not solved: {result.status.value} ({result.error})")
    print(f"  video: {video.get('video_path')}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
