#!/usr/bin/env python3
"""Live "solver on tap" — solve a captcha on a tab another driver owns.

This is the OpenSesame MCP usage shape end to end. Two independent CDP
connections to ONE Chrome:

  1. **Primary driver** — stands in for the agent's browser MCP (VoidCrawl /
     Playwright). It launches Chrome with a pinned remote-debugging port, opens
     the captcha page, and exposes the attach coordinates ``{ws_url, target_id}``
     (exactly what VoidCrawl MCP's ``session_open`` now returns).

  2. **Solver on tap** — the OpenSesame MCP ``solve`` tool. It attaches to the
     SAME Chrome, ADOPTS that exact tab by ``target_id`` (no new tab), solves the
     captcha in place, and detaches without closing the browser. The primary
     driver then sees the solved widget and can submit.

Target: 2Captcha's RotateCaptcha demo — a closed-loop oracle (rotate until the
demo's own "Check" accepts), so success is deterministic: no ML model, no
behavioural anti-bot layer, no special launch flags.

Run (unified solver venv)::

    .../venvs/solver/bin/python examples/solve_on_tap_live.py
"""

from __future__ import annotations

import asyncio
import sys

# The exact function the OpenSesame MCP exposes as the `solve` tool.
from OpenSesame.mcp.server import solve

DEMO = "https://2captcha.com/demo/rotatecaptcha"
PORT = 9344


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    # ── 1. Primary driver: owns the browser, exposes attach coordinates ──────
    async with BrowserSession(BrowserConfig(
        headless=True, stealth=True, port=PORT, extra_args=["--window-size=1280,900"],
    )) as driver:
        ws_url = await driver.websocket_url()
        page = await driver.new_page("about:blank")
        await page.goto(DEMO, timeout=45)
        await asyncio.sleep(2.5)  # let the rotate widget mount
        target_id = await page.target_id()

        print("primary driver is on the tab:")
        print(f"  ws_url    = {ws_url}")
        print(f"  target_id = {target_id}\n")

        # ── 2. Solver on tap: the OpenSesame MCP tool, attaching from outside ─
        print("calling OpenSesame MCP solve(family='rotate') ...")
        result = await solve(ws_url=ws_url, target_id=target_id, family="rotate", timeout=120)

        # ── 3. Primary driver inspects the now-solved tab ────────────────────
        final_url = await page.url()

    print(f"\nsolve result: {result}\n")
    if result.get("ok"):
        print(f"✓ PASSED — solved on a tab the solver did NOT own "
              f"(answer={result.get('answer')}deg, "
              f"by={result.get('solved_by')}, {result.get('elapsed_ms', 0):.0f}ms). "
              f"Primary driver still on {final_url}")
        return 0
    print(f"✗ not solved: status={result.get('status')} error={result.get('error')!r}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
