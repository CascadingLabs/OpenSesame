#!/usr/bin/env python3
"""Live GeeTest v3 slider solve through the OpenSesame public API.

Drives 2Captcha's GeeTest demo (https://2captcha.com/demo/geetest). GeeTest v3
runs in the main document as same-origin canvases, so OpenSesame finds the puzzle
gap by a canvas pixel-diff (no model) and drags the piece with a human-like
trajectory. The geometry is solved here; GeeTest v3 additionally scores the
*session* behaviourally and, on an automated headless run, rejects it (no
validate token) — that is the anti-bot track, reported honestly.

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_geetest_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from OpenSesame import Challenge, Family, SolverPolicy
from OpenSesame.api.defaults import default_solver

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recording import ScreenshotRecorder, SerializedPage  # noqa: E402

DEMO = "https://2captcha.com/demo/geetest"
VIDEO = Path(".local/videos/geetest.mp4")
ATTEMPTS = 3


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    policy = SolverPolicy.auto_only(allow_sites=["2captcha.com"], auto_timeout_s=40.0)
    solver = default_solver(policy)
    challenge = Challenge(family=Family.GEETEST, url=DEMO, host="2captcha.com")

    async with BrowserSession(BrowserConfig(
        headless=True, stealth=True, extra_args=["--window-size=1280,900"],
    )) as browser:
        page = SerializedPage(await browser.new_page("about:blank"))
        recorder = ScreenshotRecorder(page, VIDEO, interval=0.4)
        await recorder.start()
        result = None
        try:
            for attempt in range(1, ATTEMPTS + 1):
                await page.goto(DEMO, timeout=45)
                try:
                    await page.wait_for_selector(".geetest_radar_btn", timeout=30)
                except Exception:
                    pass
                await asyncio.sleep(2.0)
                result = await solver.solve(challenge, page=page, timeout=45)
                meta = result.metadata
                print(f"  attempt {attempt}: status={result.status.value} "
                      f"gap_left={meta.get('gap_left')} drag={meta.get('drag_css')}")
                if result.ok:
                    break
        finally:
            video = await recorder.stop()

    if result and result.ok:
        print(f"✓ PASSED — geetest_validate minted ({len(result.token)} chars)")
    else:
        meta = result.metadata if result else {}
        print(f"✗ geometry solved (gap_left={meta.get('gap_left')}, drag={meta.get('drag_css')}px); "
              f"GeeTest v3 behavioural layer rejected the session (route={meta.get('route')}).")
    print(f"  video: {video.get('video_path')}")
    return 0 if (result and result.ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
