#!/usr/bin/env python3
"""Live Cloudflare **Managed Challenge** traversal through the OpenSesame API.

Variant 2 of two (see `solve_turnstile_widget_live.py` for the embedded widget). The
full-page "Just a moment…" interstitial
(https://2captcha.com/demo/cloudflare-turnstile-challenge) is **not a token and not a
click** — it is an edge-enforced browser-trust gate decided by **CDP/automation
detection**. A browser that enables almost no CDP domain auto-clears it in seconds; a
loud CDP session never does. So OpenSesame does *not* click — it detects the
interstitial and **awaits the clearance** the minimal-stealth browser produces.

The key is the launch env: ``VOIDCRAWL_STEALTH_NO_RUNTIME`` makes VoidCrawl skip the
eager CDP-instrumentation tells (Runtime / Network / Performance / Log / autoAttach /
isolated-world enables), matching a clean browser. With it, the wall clears in ~4s
(verified); without it, it stays walled forever. (CAS-217.)

Run (needs the `live` extra; uses the unified solver venv). Headful — on a headless
box wrap with `xvfb-run`:

    PYTHONPATH=src xvfb-run -a .../venvs/solver/bin/python examples/solve_turnstile_challenge_live.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Minimal-CDP-footprint browser — the env that lets Cloudflare's Managed Challenge
# clear. Must be set before VoidCrawl launches Chrome (the vendored chromiumoxide
# reads it at session init).
os.environ.setdefault("VOIDCRAWL_STEALTH_NO_RUNTIME", "1")

from OpenSesame import Challenge, SolverPolicy  # noqa: E402
from OpenSesame.api.defaults import default_solver  # noqa: E402

DEMO = "https://2captcha.com/demo/cloudflare-turnstile-challenge"


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(allow_sites=["2captcha.com"]))

    # Headful + no extra flags: in minimal-stealth mode we want the quietest possible
    # CDP surface (no disable-site-isolation-trials — that flag is itself a tell, and
    # the managed challenge needs no cross-origin frame access).
    async with BrowserSession(BrowserConfig(
        headless=False, stealth=True, extra_args=["--window-size=1366,900"],
    )) as browser:
        page = await browser.new_page("about:blank")
        # Network domain is off in stealth mode, so don't wait on network-idle.
        nav = getattr(page, "navigate", None)
        if callable(nav):
            await page.navigate(DEMO)
        else:                                          # older VoidCrawl
            try:
                await page.goto(DEMO, timeout=10)
            except Exception:
                pass

        kind = await page.detect_captcha()             # "turnstile"
        challenge = Challenge.from_capture({"kind": kind or "turnstile", "page_url": DEMO})
        result = await solver.solve(challenge, page=page, timeout=40)

    if result.ok:
        print("✓ PASSED — the minimal-stealth browser cleared the Cloudflare managed "
              f"challenge (cleared={result.metadata.get('cleared')}, no click, no token)")
        return 0
    print(f"✗ not cleared: {result.status.value} ({result.error})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
