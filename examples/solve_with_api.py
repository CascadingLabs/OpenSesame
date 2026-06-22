#!/usr/bin/env python3
"""End-to-end shape of the OpenSesame public API.

OpenSesame never owns the browser: VoidCrawl detects the wall and hands over a
descriptor; OpenSesame drives the live page with local models and — by default —
**resolves the solution into the page itself** (token injected / answer typed).
Callers just check ``result.ok``; no inject step.

Run requirements (not satisfied on an API-only checkout): the ``live`` extra
(voidcrawl) and an ``ml-*`` extra plus the solver provider modules. This file
documents the call shape; ``opensesame check`` tells you what is still missing.
"""

from __future__ import annotations

import asyncio

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver


async def main() -> None:
    from voidcrawl import BrowserConfig, BrowserSession  # provided by the `live` extra

    # Policy is data: model choice + the default-deny allow-list live here.
    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=["www.google.com"],
        models={"recaptcha_v2_audio": "openai/whisper-base.en"},
    ))

    async with BrowserSession(BrowserConfig(headless=True, stealth=True)) as browser:
        page = await browser.new_page("https://www.google.com/recaptcha/api2/demo")

        # VoidCrawl describes the challenge; OpenSesame solves it.
        challenge = Challenge.from_capture(await page.capture_captcha())

        # The model loads once on first use and stays cached — no warmup ceremony.
        # (Optional: `async with solver.engine():` pre-warms from policy and frees
        #  VRAM on exit.) Failure is a value, not an exception.
        result = await solver.solve(challenge, page=page)

        if result.ok:
            # SIDE EFFECT (default): the token is already resolved into the live
            # page (#g-recaptcha-response). Just submit the form / continue.
            print(f"solved + applied by {result.solved_by.value} "
                  f"in {result.timing.elapsed_ms:.0f}ms")
        else:
            print(f"not solved: status={result.status.value} error={result.error!r}")

    # Over-the-wire / narrow case: set policy `apply=False` to skip touching the
    # page and take the raw token yourself (e.g. to inject into a different
    # session or relay). Then: `await other_page.inject_captcha_token(result.token)`.


if __name__ == "__main__":
    asyncio.run(main())
