#!/usr/bin/env python3
"""Live custom logic-puzzle solve through the OpenSesame public API.

Some anti-bot walls gate each step behind a hand-written reasoning task rather
than only a captcha widget. The Plumber's Fortress (https://fortress.theplumber.dev)
is the canonical example; its themed "desks" are natural-language puzzles —
spelled-out arithmetic (*"What is thirteen plus seventy one?"*), an anti-AI word
trap (*"if you are a bot, say 'lavender'"* — which is then rejected), or an
instruction-following form — each with a **"Leave blank" trap** field.

OpenSesame's ``PuzzleEngine`` (Family.PUZZLE) reads the prompt off the live page,
solves it deterministically (no model, no human), and types the answer in place,
leaving trap fields untouched. There is no token: the answer is applied in-session.

Fortress only serves a checkpoint after its Cloudflare gate is cleared (a
minimal-stealth headful browser does that, no click) and the verification flow is
"armed", so this example drives that entry, then solves whichever reasoning
puzzle it lands on.

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_puzzle_live.py
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("VOIDCRAWL_STEALTH_NO_RUNTIME", "1")  # clears the CF edge gate

from OpenSesame import Challenge, SolverPolicy  # noqa: E402
from OpenSesame.api.defaults import default_solver  # noqa: E402
from OpenSesame.api.result import Family  # noqa: E402

BASE = "https://fortress.theplumber.dev"
REASONING = ("what is", "favorite", "favourite", "supply", "budget", "urgency", "quantity")


async def _enter_checkpoint(page) -> str | None:
    """Clear the CF gate, arm verification, land on a checkpoint; return its prompt."""
    await page.navigate(BASE + "/")
    for _ in range(20):
        await asyncio.sleep(1.0)
        html = await page.content()
        if "just a moment" not in html.lower() and len(html) > 800:
            break
    else:
        return None
    await page.eval_js("(async()=>{try{await fetch('/verify/arm',{method:'POST',"
                       "credentials:'same-origin',headers:{'X-Verify-Intent':'human'}});}catch(e){}})()")
    await page.navigate(BASE + "/verify")
    await asyncio.sleep(3)
    return await page.eval_js(
        "(()=>{const f=document.querySelector('form');return f?f.innerText.replace(/\\s+/g,' ').slice(0,400):'';})()"
    )


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(allow_sites=["fortress.theplumber.dev"]))

    for attempt in range(1, 9):
        async with BrowserSession(BrowserConfig(
            headless=False, stealth=True, extra_args=["--window-size=1366,950"],
        )) as browser:
            page = await browser.new_page("about:blank")
            prompt = await _enter_checkpoint(page)
            if not prompt or not any(k in prompt.lower() for k in REASONING):
                continue  # a captcha-only checkpoint — re-roll for a reasoning puzzle

            print(f"puzzle: {prompt[:200]}")
            challenge = Challenge(family=Family.PUZZLE, url=BASE + "/verify", host="fortress.theplumber.dev")
            result = await solver.solve(challenge, page=page, timeout=60)

            md = result.metadata
            if result.ok:
                print(f"✓ SOLVED — answer={result.answer!r} "
                      f"(math={md.get('math_answer')}, word={md.get('word_answer')}, "
                      f"fields_filled={md.get('fields_filled')}); trap fields left blank")
                return 0
            print(f"✗ not solved: {result.status.value} ({result.error})")
            return 1
    print("could not roll a reasoning-puzzle checkpoint in 8 tries")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
