#!/usr/bin/env python3
"""Live reCAPTCHA v2 solve via the audio side-door, through the public API.

Drives **real Google reCAPTCHA** (api2/demo) and mints a real
`g-recaptcha-response` token, fully locally: OpenSesame opens the challenge,
reads the signed MP3 from the same-origin DOM, transcribes it with a local
Whisper model, types + verifies, and (apply=True) lands the token in the page.
No paid solver API, and no glue here — the Whisper provider is built in.

Run (needs the `live` + `ml-audio` extras; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_audio_live.py
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
        models={"recaptcha_v2_audio": "openai/whisper-base.en", "recaptcha_v2_strategy": "audio"},
    ))

    async with BrowserSession(BrowserConfig(headless=True, stealth=True,
                                            extra_args=["--window-size=1365,900"])) as browser:
        page = await browser.new_page(DEMO)
        async with solver.engine():           # warm Whisper once
            result = await solver.solve(
                Challenge.from_capture({"kind": "recaptcha", "page_url": DEMO}),
                page=page, timeout=120,
            )

    if result.ok:
        print(f"✓ PASSED — minted a real reCAPTCHA token ({len(result.token)} chars, "
              f"applied={result.applied}, {result.timing.elapsed_ms:.0f}ms)")
        return 0
    print(f"✗ not solved: {result.status.value} ({result.error})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
