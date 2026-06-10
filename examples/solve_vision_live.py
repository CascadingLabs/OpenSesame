#!/usr/bin/env python3
"""Live reCAPTCHA v2 image-grid solve through the OpenSesame public API.

Drives **real Google reCAPTCHA** (api2/demo): OpenSesame opens the challenge,
reads the grid DOM, classifies each tile with a local ViT (``verytuffcat/recaptcha``,
built in — no glue here), clicks the matching cells via the DOM, verifies, and
harvests the token, retrying across reCAPTCHA's chained challenges.

Reality check: the image grid is materially harder than the audio side-door.
Classification is strong, but reCAPTCHA only mints a token when the selected set
*exactly* matches its expectation (ambiguous boundary tiles) and its risk engine
is satisfied — so a token is not guaranteed. The audio example is the reliable
local reCAPTCHA solve; this one is a genuine live attempt.

Run (needs the `live` + `ml-vision` extras; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_vision_live.py
"""

from __future__ import annotations

import asyncio
import sys

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver

DEMO = "https://www.google.com/recaptcha/api2/demo"


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=["www.google.com"],
        models={"recaptcha_v2_grid": "verytuffcat/recaptcha", "recaptcha_v2_strategy": "grid"},
    ))

    async with BrowserSession(BrowserConfig(headless=True, stealth=True,
                                            extra_args=["--window-size=1365,900"])) as browser:
        page = await browser.new_page(DEMO)
        # Build the challenge from VoidCrawl's live DOM probe (the production
        # descriptor path) rather than hand-asserting the kind.
        kind = await page.detect_captcha()    # -> "recaptcha"
        challenge = Challenge.from_capture({"kind": kind or "recaptcha", "page_url": DEMO})
        async with solver.engine():           # warm the ViT once
            for attempt in range(1, 9):       # retry across chained challenges
                result = await solver.solve(challenge, page=page, timeout=120)
                print(f"  attempt {attempt}: {'token' if result.ok else result.status.value}")
                if result.ok:
                    print(f"\n✓ PASSED — minted a reCAPTCHA token via the image grid "
                          f"({len(result.token)} chars, {result.timing.elapsed_ms:.0f}ms)")
                    return 0
                await asyncio.sleep(1.5)

    print("\n✗ no token within budget — reCAPTCHA refused the selection "
          "(boundary tiles / risk engine). Use the audio side-door for a reliable solve.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
