#!/usr/bin/env python3
"""Live hCaptcha canvas solve through the OpenSesame public API.

Drives the **real hCaptcha demo** (publisher sitekey ``a5f74b19-…`` — genuine
challenges, not the dummy test key). hCaptcha paints the whole challenge to a
``<canvas>`` and asks a semantic question ("choose the card that shows a different
animal"), so OpenSesame:

  - clicks the checkbox to open the challenge,
  - screenshots the canvas ("point-in-time imaging"),
  - asks a local VLM (``Qwen/Qwen2.5-VL-7B-Instruct``) to *ground* the cell to click,
  - humanized-clicks that point on the canvas, submits, loops the rounds,
  - harvests the ``h-captcha-response`` token.

The demo's challenges are **animated** ("the card that *changes* / shows a *different*
animal") — the cards reveal their animals one at a time, so no single frame shows them
all. The engine captures a short **burst** and the VLM provider composites it (per-pixel
median background + max-deviation) into one image where every revealed cell appears at
once, then grounds the odd-one-out in a single pass. Validated live: the 3B picks the
correct card (~12s/round on an AMD 890M iGPU). Per-round latency + point land in
``result.metadata["rounds"]``.

Runs against the **headful VoidCrawl docker container** (real GPU Chrome, the stealthy
path — `docker/run-headful.sh`, CDP on :19222), attaching over CDP. Needs the `live` +
`ml-vision` extras and the VLM cached (``opensesame download hcaptcha``):

    PYTHONPATH=src .../venvs/rocm/bin/python examples/solve_hcaptcha_live.py
"""

from __future__ import annotations

import asyncio
import sys

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver

DEMO = "https://accounts.hcaptcha.com/demo"
MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"


def _summarize_rounds(result) -> str:
    rounds = (result.metadata or {}).get("rounds") or []
    return " | ".join(
        f"r{r['round']}: {r.get('infer_ms', '?')}ms point={r.get('point')}"
        for r in rounds
    ) or "(no rounds recorded)"


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=["accounts.hcaptcha.com"],
        models={"hcaptcha": MODEL},
        auto_timeout_s=180.0,        # a 7B VLM over two rounds is not instant
    ))

    async with BrowserSession(BrowserConfig(headless=True, stealth=True,
                                            extra_args=["--window-size=1365,900"])) as browser:
        page = await browser.new_page(DEMO)
        kind = await page.detect_captcha()    # -> "hcaptcha"
        challenge = Challenge.from_capture({"kind": kind or "hcaptcha", "page_url": DEMO})
        async with solver.engine():           # warm the VLM once
            for attempt in range(1, 4):       # rerolls are cheap; give it a few tries
                result = await solver.solve(challenge, page=page, timeout=180)
                print(f"  attempt {attempt}: {result.status.value} — {_summarize_rounds(result)}")
                if result.ok:
                    print(f"\n✓ PASSED — minted an h-captcha-response "
                          f"({len(result.token)} chars, {result.timing.elapsed_ms:.0f}ms total)")
                    return 0
                if result.error:
                    print(f"     ↳ {result.error}")
                await asyncio.sleep(1.5)

    print("\n✗ no token within budget — the VLM's picks didn't satisfy hCaptcha. "
          "Inspect metadata.rounds and tune the grounding prompt / model.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
