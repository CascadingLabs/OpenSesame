#!/usr/bin/env python3
"""Live Cloudflare Turnstile **widget** solve through the OpenSesame public API.

Variant 1 of two (see `solve_turnstile_challenge_live.py` for the full-page Managed
Challenge). Drives a **real site** — 2Captcha's embedded Turnstile demo
(https://2captcha.com/demo/cloudflare-turnstile) — where the page is on `2captcha.com`
but the Turnstile widget iframe is cross-origin from `challenges.cloudflare.com`.
OpenSesame locates the "Verify you are human" checkbox — a real `<input>` in a
**closed shadow root** inside that cross-origin frame — via VoidCrawl 0.3.6's
accessibility locator, drives a **humanized** compositor click, and harvests the
minted `cf-turnstile-response` token. No model, no glue.

Cross-origin prerequisite: launch with `extra_args=["disable-site-isolation-trials"]`
so the Cloudflare frame stays in-process and AX-reachable.

Note: the demo uses a Cloudflare *test* sitekey (`3x…FF`), so the token is a dummy
(`XXXX.DUMMY.TOKEN.XXXX`) — it proves the click/harvest mechanics; a real token also
depends on IP/browser reputation.

Run (needs the `live` extra; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_turnstile_widget_live.py
"""

from __future__ import annotations

import asyncio
import sys

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver

DEMO = "https://2captcha.com/demo/cloudflare-turnstile"


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(allow_sites=["2captcha.com"]))

    async with BrowserSession(BrowserConfig(
        headless=True, stealth=True,
        extra_args=["--window-size=1280,1000", "disable-site-isolation-trials"],
    )) as browser:
        page = await browser.new_page("about:blank")
        for attempt in range(1, 6):
            try:
                await page.goto(DEMO, timeout=40)
                break
            except Exception as exc:                # transient network; retry
                print(f"  attempt {attempt}: navigation error ({exc}), retrying")
                await asyncio.sleep(2)
        await asyncio.sleep(4)                       # let Turnstile initialize

        kind = await page.detect_captcha()           # VoidCrawl live probe -> "turnstile"
        challenge = Challenge.from_capture({"kind": kind or "turnstile", "page_url": DEMO})
        result = await solver.solve(challenge, page=page, timeout=90)

    if result.ok:
        print(f"✓ PASSED — clicked the closed-shadow Turnstile checkbox cross-origin and "
              f"minted cf-turnstile-response ({len(result.token)} chars, "
              f"applied={result.applied})")
        return 0
    print(f"✗ not solved: {result.status.value} ({result.error})")
    if result.metadata.get("frame_isolated"):
        print("  hint: launch with extra_args=[\"disable-site-isolation-trials\"]")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
