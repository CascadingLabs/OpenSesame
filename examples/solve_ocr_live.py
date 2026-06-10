#!/usr/bin/env python3
"""Live OCR captcha solve through the OpenSesame public API.

Drives a **real site** — 2Captcha's normal-captcha demo
(https://2captcha.com/demo/normal) — and clears it server-side: OpenSesame reads
the distorted-text image with a local captcha-OCR model (``Graf-J``, built in),
the answer is typed into the form (apply=True), and the page is submitted; the
demo replies "Captcha is passed successfully!". No glue here — just name the
model in policy.

Run (needs the `live` + `ml-vision` extras; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_ocr_live.py
"""

from __future__ import annotations

import asyncio
import sys

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver

DEMO = "https://2captcha.com/demo/normal"
CAPTCHA_IMG = 'img[alt="normal captcha example"]'
ANSWER_FIELD = "#simple-captcha-field"
# Pin a reproducible, offline-cacheable revision of the captcha-OCR model.
OCR_MODEL = "Graf-J/captcha-conv-transformer-base@1896f25517e3e9c2905db37863bc18e774759646"


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=["2captcha.com"],
        models={"ocr": OCR_MODEL},
    ))

    async with BrowserSession(BrowserConfig(headless=True, stealth=True)) as browser:
        page = await browser.new_page("about:blank")
        async with solver.engine():            # warm the OCR model once
            for attempt in range(1, 6):
                try:
                    await page.goto(DEMO, timeout=40)
                except Exception as exc:       # transient network; refresh + retry
                    print(f"  attempt {attempt}: navigation error ({exc}), retrying")
                    await asyncio.sleep(2)
                    continue
                challenge = Challenge.ocr(
                    url=DEMO, image_selector=CAPTCHA_IMG, response_field_selector=ANSWER_FIELD,
                )
                result = await solver.solve(challenge, page=page, timeout=60)
                if not result.ok:
                    print(f"  attempt {attempt}: OCR low-confidence, retrying")
                    continue

                # The answer was typed by apply=True; submit the form + read the verdict.
                await page.eval_js(
                    f"document.querySelector('{ANSWER_FIELD}').closest('form').requestSubmit()"
                )
                await asyncio.sleep(2.0)
                body = str(await page.eval_js("document.body.innerText"))
                passed = "Captcha is passed successfully!" in body
                print(f"  attempt {attempt}: read={result.answer!r} -> "
                      f"{'passed' if passed else 'rejected'}")
                if passed:
                    print(f"\n✓ PASSED — OCR read '{result.answer}', the live site accepted it "
                          f"({result.timing.elapsed_ms:.0f}ms)")
                    return 0
                await asyncio.sleep(1.0)

    print("\n✗ did not clear the captcha within attempt budget")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
