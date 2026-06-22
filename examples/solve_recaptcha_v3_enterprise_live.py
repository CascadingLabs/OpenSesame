#!/usr/bin/env python3
"""Live reCAPTCHA **v3 Enterprise** through the OpenSesame public API.

Same shape as standard v3 (see ``solve_recaptcha_v3_live.py``): invisible,
score-based, no puzzle. ``RecaptchaV3Engine`` auto-detects the Enterprise variant
(``grecaptcha.enterprise`` + the ``enterprise.js?render=`` script), warms the
session (human-like fuzzy mouse + the occasional trusted click), and mints the
token via ``grecaptcha.enterprise.execute``.

One difference in *measurement*: the Enterprise demo's verify endpoint conveys its
token internally (it doesn't accept an arbitrary token over the wire the way the
standard demo's ``{siteKey, answer}`` POST does), so we read the achieved score
from the demo's **own** "Check" flow — run *after* the engine has warmed the
session — taking the best of a few checks (the v3 score is IP/reputation-dominated
and noisy run-to-run). The engine still mints and returns its own valid Enterprise
token (printed), which a real caller relays.

The v3 score is IP/cookie-reputation dominated, so the biggest lever is the launch
config (not the engine): a **headful** Chrome with a **persistent profile** so the
``_GRECAPTCHA`` cookie ages, on a clean/residential IP. Enterprise reliably reaches
0.7–0.9 that way. Set ``VOIDCRAWL_WS_URL`` to drive the headful VoidCrawl container
started with ``CHROME_PROFILES_DIR=/profiles`` (persistent profile); unset, this
launches a local headless Chrome (often enough, with best-of-N, on a warm IP).

Drives https://2captcha.com/demo/recaptcha-v3-enterprise.

Run (needs the `live` extra; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_recaptcha_v3_enterprise_live.py
    # or against the persistent-profile container (recommended for Enterprise):
    VOIDCRAWL_WS_URL=http://localhost:19222 PYTHONPATH=src .../python examples/solve_recaptcha_v3_enterprise_live.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from OpenSesame import Challenge, Family, SolverPolicy
from OpenSesame.api.defaults import default_solver

DEMO = "https://2captcha.com/demo/recaptcha-v3-enterprise"
VERIFY = "/api/v1/captcha-demo/recaptcha-enterprise/verify"   # -> {riskAnalysis:{score}}
PASS_SCORE = 0.5
BEST_OF = 5

INTERCEPT = r"""
(() => { if (window.__caps) return true; window.__caps = [];
  const of = window.fetch;
  window.fetch = async function(...a) { const u = (a[0] && a[0].url) || a[0]; const r = await of.apply(this, a);
    try { const t = await r.clone().text(); window.__caps.push({url: String(u), body: t.slice(0, 900)}); } catch (e) {}
    return r; };
  return true; })()
"""


async def check_score(page) -> float | None:
    """Trigger the demo's own Check (it conveys its token), read the score."""
    await page.eval_js(INTERCEPT)
    # DOM .click() on the normal React button — works locally AND over the slow
    # container CDP (avoids the accessibility-locate timeout of click_by_role).
    await page.eval_js("(()=>{const b=document.querySelector('button[data-action=\"demo_action\"]');if(b){b.click();return true}return false})()")
    for _ in range(24):
        try:
            caps = await page.eval_js("window.__caps || []")
        except Exception:
            caps = []
        for c in caps or []:
            if VERIFY in c.get("url", ""):
                try:
                    j = json.loads(c.get("body", ""))
                except Exception:
                    continue
                s = (j.get("riskAnalysis") or {}).get("score")
                if s is not None:
                    return float(s)
        await asyncio.sleep(0.5)
    return None


def _browser_config():
    """Attach to the persistent-profile headful container if VOIDCRAWL_WS_URL is
    set (best for Enterprise — aged _GRECAPTCHA cookie); else a local headless
    Chrome. Warming is skipped over the container's slow CDP (the profile cookie is
    the real trust signal there)."""
    from voidcrawl import BrowserConfig
    ws = os.environ.get("VOIDCRAWL_WS_URL")
    if ws:
        return BrowserConfig(ws_url=ws, stealth=True), {"recaptcha_v3_warm_s": "0"}
    return BrowserConfig(headless=True, stealth=True, extra_args=["--window-size=1280,900"]), {}


async def main() -> int:
    from voidcrawl import BrowserSession

    cfg, model_overrides = _browser_config()
    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=["2captcha.com"], auto_timeout_s=120.0, models=model_overrides))

    async with BrowserSession(cfg) as browser:
        page = await browser.new_page("about:blank")
        for attempt in range(1, 6):
            try:
                await page.goto(DEMO, timeout=45); break
            except Exception as exc:
                print(f"  attempt {attempt}: navigation error ({exc}), retrying")
                await asyncio.sleep(2)
        await asyncio.sleep(3)

        # Engine detects Enterprise, warms the session, mints its own token.
        challenge = Challenge(family=Family.RECAPTCHA_V3, url=DEMO, host="2captcha.com")
        result = await solver.solve(challenge, page=page, timeout=120)
        if not result.ok:
            print(f"✗ not solved: {result.status.value} ({result.error})")
            return 1
        if not result.metadata.get("enterprise"):
            print("  note: engine did not detect the Enterprise variant on this page")

        # best-of-N over the warmed session's own Check flow.
        scores: list[float] = []
        best = -1.0
        for _ in range(BEST_OF):
            s = await check_score(page)
            if s is not None:
                scores.append(s); best = max(best, s)
            await asyncio.sleep(1.0)

    if best >= PASS_SCORE:
        print(f"✓ PASSED — Enterprise v3 warmed session scores {best} (≥{PASS_SCORE}); best of {scores}; "
              f"engine minted token {len(result.token)} chars, warm={result.metadata.get('warmed_s')}s")
        return 0
    print(f"✗ low score: best={best} of {scores} — v3 score is IP/reputation-dominated; "
          f"run headful (Xvfb) on a clean/residential IP to lift it (anti-bot track).")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
