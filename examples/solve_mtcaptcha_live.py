#!/usr/bin/env python3
"""Live MTCaptcha solve through the OpenSesame public API — mints a real token.

Drives 2Captcha's MTCaptcha demo (https://2captcha.com/demo/mtcaptcha). The word
is drawn over a photo inside a cross-origin `service.mtcaptcha.com` iframe;
OpenSesame OCRs it with a local scene-text recognizer, types it in-frame with real
CDP keys, and harvests the minted `mtcaptcha-verifiedtoken`. No paid API.

Cross-origin prerequisite: launch with `extra_args=["disable-site-isolation-trials"]`
so the widget frame stays in-process (keystrokes reach its input).

Run (needs voidcrawl + the scene-text venv; the solver venv works):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_mtcaptcha_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from OpenSesame import Challenge, Family, SolverPolicy
from OpenSesame.api.defaults import default_solver

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recording import ScreenshotRecorder, SerializedPage  # noqa: E402

DEMO = "https://2captcha.com/demo/mtcaptcha"
VIDEO = Path(".local/videos/mtcaptcha.mp4")


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    policy = SolverPolicy.auto_only(allow_sites=["2captcha.com"], auto_timeout_s=220.0)
    solver = default_solver(policy)

    async with BrowserSession(BrowserConfig(
        headless=True, stealth=True,
        extra_args=["--window-size=1280,900", "disable-site-isolation-trials"],
    )) as browser:
        page = SerializedPage(await browser.new_page("about:blank"))
        recorder = ScreenshotRecorder(page, VIDEO, interval=0.5)
        await recorder.start()
        try:
            await page.goto(DEMO, timeout=45)
            try:
                await page.wait_for_selector('iframe[src*="mtcaptcha"]', timeout=30)
            except Exception:
                pass
            await asyncio.sleep(2.5)
            challenge = Challenge(family=Family.MTCAPTCHA, url=DEMO, host="2captcha.com")
            result = await solver.solve(challenge, page=page, timeout=230)
            if result.ok:
                # Press the demo's own "Check" so the green confirmation lands on video.
                try:
                    await page.click_by_role("button", "Check")
                    await asyncio.sleep(2.0)
                except Exception:
                    pass
        finally:
            video = await recorder.stop()

    if result.ok:
        print(f"✓ PASSED — minted mtcaptcha-verifiedtoken ({len(result.token)} chars) "
              f"in {result.metadata.get('attempts')} attempts")
        print(f"  video: {video.get('video_path')}")
        return 0
    print(f"✗ not solved: {result.status.value} ({result.error})")
    print(f"  video: {video.get('video_path')}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
