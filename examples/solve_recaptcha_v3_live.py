#!/usr/bin/env python3
"""Live reCAPTCHA **v3** (standard) through the OpenSesame public API.

v3 is invisible and score-based: there is no puzzle and no click. ``grecaptcha``
mints a token every time, carrying a 0.0–1.0 trust **score** the site checks
server-side (≈0.5 passes). "Solving" v3 = making the browser environment trusted
enough that the minted token scores high. OpenSesame's ``RecaptchaV3Engine`` owns
the half it can drive from the page: it **warms** the session (human-like fuzzy
mouse motion + the occasional trusted click) and **mints** the token cleanly via
``grecaptcha.execute`` (auto-discovering the sitekey + action from the page).

The score is decided by Google from the browser fingerprint, behaviour, and —
dominantly — **IP/cookie reputation**, so it is noisy run-to-run on a shared demo
IP. This example takes the **best of a few mints** (the right move for a noisy
score) and posts OpenSesame's *own* minted token to 2Captcha's verify endpoint to
read its score. The biggest lever is the launch config (the engine never owns the
browser): a headful Chrome with a persistent profile so the ``_GRECAPTCHA`` cookie
ages, on a clean/residential IP. Set ``VOIDCRAWL_WS_URL`` to drive the headful
VoidCrawl container (``CHROME_PROFILES_DIR=/profiles``); unset, this launches a
local headless Chrome (enough for standard v3 with best-of-N on a warm IP).

Drives 2Captcha's standard v3 demo (https://2captcha.com/demo/recaptcha-v3).

Run (needs the `live` extra; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_recaptcha_v3_live.py
    # or against the persistent-profile container:
    VOIDCRAWL_WS_URL=http://localhost:19222 PYTHONPATH=src .../python examples/solve_recaptcha_v3_live.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from OpenSesame import Challenge, Family, SolverPolicy
from OpenSesame.api.defaults import default_solver

DEMO = "https://2captcha.com/demo/recaptcha-v3"
VERIFY = "/api/v1/captcha-demo/recaptcha/verify"   # POST {siteKey, answer: token} -> {score}
PASS_SCORE = 0.5
BEST_OF = 5


async def mint(page, sitekey: str, action: str) -> str:
    js = f"""
    (async () => {{ try {{ await new Promise(r => grecaptcha.ready(r));
      return await grecaptcha.execute({json.dumps(sitekey)}, {{action: {json.dumps(action)}}}); }}
      catch (e) {{ return ''; }} }})()
    """
    return str(await page.eval_js(js) or "")


async def score_of(page, sitekey: str, token: str) -> float | None:
    """Post OpenSesame's own token to the demo verify endpoint; read its score."""
    js = f"""
    (async () => {{ const r = await fetch({json.dumps(VERIFY)}, {{ method:'POST',
        headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{siteKey: {json.dumps(sitekey)}, answer: {json.dumps(token)}}}) }});
      return await r.json().catch(() => null); }})()
    """
    j = await page.eval_js(js)
    return float(j["score"]) if isinstance(j, dict) and j.get("score") is not None else None


def _browser_config():
    """Attach to the persistent-profile headful container if VOIDCRAWL_WS_URL is
    set (aged _GRECAPTCHA cookie); else a local headless Chrome. Warming is skipped
    over the container's slow CDP (the profile cookie is the real trust signal)."""
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

        # Engine warms the session + mints the first token (auto-discovers sitekey/action).
        challenge = Challenge(family=Family.RECAPTCHA_V3, url=DEMO, host="2captcha.com")
        result = await solver.solve(challenge, page=page, timeout=120)
        if not result.ok:
            print(f"✗ not solved: {result.status.value} ({result.error})")
            return 1
        sitekey = str(result.metadata.get("sitekey"))
        action = str(result.metadata.get("action") or "demo_action")

        # best-of-N: score the engine's first token, then a few more fresh mints.
        scores: list[float] = []
        best, best_token = -1.0, result.token
        for k in range(BEST_OF):
            token = result.token if k == 0 else await mint(page, sitekey, action)
            s = await score_of(page, sitekey, token) if token else None
            if s is not None:
                scores.append(s)
                if s > best:
                    best, best_token = s, token
            await asyncio.sleep(1.0)

    if best >= PASS_SCORE:
        print(f"✓ PASSED — minted a v3 token that scores {best} (≥{PASS_SCORE}); "
              f"best of {scores}; token {len(best_token)} chars, warm={result.metadata.get('warmed_s')}s")
        return 0
    print(f"✗ low score: best={best} of {scores} — v3 score is IP/reputation-dominated; "
          f"run headful (Xvfb) on a clean/residential IP to lift it (anti-bot track).")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
