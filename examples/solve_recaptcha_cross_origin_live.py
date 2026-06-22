#!/usr/bin/env python3
"""Live reCAPTCHA v2 solve on a **real third-party site** — cross-origin frame.

Unlike `solve_audio_live.py` (Google's own `api2/demo`, where the challenge frame
is same-origin), this drives `2captcha.com/demo/recaptcha-v2`: the page is on
`2captcha.com` but reCAPTCHA's `bframe`/`anchor` iframes are served from
`google.com` — **cross-origin**, so `iframe.contentDocument` is `null` to the
page. OpenSesame drives the challenge through VoidCrawl 0.3.5's frame-scoped eval
(`eval_js_in_frame`), reading the signed audio URL from inside the cross-origin
frame and minting a real `g-recaptcha-response` token, fully locally.

Cross-origin prerequisite: the session is launched with
`extra_args=["disable-site-isolation-trials"]` so Chrome keeps the google.com
frames in-process (otherwise they are out-of-process and unreachable).

Run (needs the `live` + `ml-audio` extras; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_recaptcha_cross_origin_live.py
"""

from __future__ import annotations

import asyncio
import sys

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver

DEMO = "https://2captcha.com/demo/recaptcha-v2"


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=["2captcha.com"],
        # Audio is the reliable path; it now works cross-origin too.
        models={"recaptcha_v2_audio": "openai/whisper-base.en", "recaptcha_v2_strategy": "audio"},
    ))

    async with BrowserSession(BrowserConfig(
        headless=True, stealth=True,
        # The opt-in that keeps the cross-origin google.com frames reachable.
        extra_args=["--window-size=1365,900", "disable-site-isolation-trials"],
    )) as browser:
        page = await browser.new_page(DEMO)
        kind = await page.detect_captcha()        # VoidCrawl live probe -> "recaptcha"
        challenge = Challenge.from_capture({"kind": kind or "recaptcha", "page_url": DEMO})
        async with solver.engine():               # warm Whisper once
            result = await solver.solve(challenge, page=page, timeout=150)

    if result.ok:
        print(f"✓ PASSED — minted a real reCAPTCHA token on a CROSS-ORIGIN site "
              f"({len(result.token)} chars, applied={result.applied}, "
              f"{result.timing.elapsed_ms:.0f}ms)")
        return 0
    # An honest, actionable failure if the isolation flag is missing.
    print(f"✗ not solved: {result.status.value} ({result.error})")
    if result.metadata.get("frame_isolated"):
        print("  hint: launch with extra_args=[\"disable-site-isolation-trials\"]")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
