#!/usr/bin/env python3
"""End-to-end shape of the OpenSesame public API.

OpenSesame never owns the browser: VoidCrawl detects the wall and hands over a
descriptor; OpenSesame solves and returns a token/answer; VoidCrawl injects it.

Run requirements (not satisfied on an API-only checkout): the ``live`` extra
(voidcrawl) and an ``ml-*`` extra plus the solver provider modules. This file
documents the call shape; ``opensesame check`` tells you what is still missing.
"""

from __future__ import annotations

import asyncio

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver
from OpenSesame.api.registry import ModelKey


async def main() -> None:
    from voidcrawl import BrowserConfig, BrowserSession  # provided by the `live` extra

    policy = SolverPolicy.auto_only(
        allow_sites=["www.google.com"],  # default-deny: only these hosts are solved
        device="auto",
        models={"recaptcha_v2_audio": "openai/whisper-base.en"},
    )
    solver = default_solver(policy)

    async with BrowserSession(BrowserConfig(headless=True, stealth=True)) as browser:
        page = await browser.new_page("https://www.google.com/recaptcha/api2/demo")

        # 1) VoidCrawl detects + describes the challenge.
        captcha_info = await page.capture_captcha()
        challenge = Challenge.from_capture(captcha_info)

        # 2) Warm the model once, then solve (failure is a value, not an exception).
        # produces a singleton for needed solves
        async with solver.engine(
            warmup=[ModelKey("whisper", "openai/whisper-base.en", "auto")]
        ):
            result = await solver.solve(challenge, page=page)

        # 3) Consume the solution. Token-grant -> inject; answer -> type.
        if result.ok and result.solution.is_token:
            await page.inject_captcha_token(result.token)
            print(
                f"solved by {result.solved_by.value} in {result.timing.elapsed_ms:.0f}ms"
            )
        else:
            print(f"not solved: status={result.status.value} error={result.error!r}")


if __name__ == "__main__":
    asyncio.run(main())
