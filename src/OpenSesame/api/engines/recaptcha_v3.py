"""reCAPTCHA v3 — invisible, score-based: warm the session, mint a clean token.

v3 has no puzzle and no click. ``grecaptcha.execute`` mints a token *every* time;
the token carries a **0.0–1.0 trust score** the site checks server-side (≈0.5 is
the usual pass line). So "solving" v3 is not cracking a challenge — it is making
the browser environment trusted enough that the minted token scores high. Google
sets the score from the browser fingerprint, behaviour, and IP/cookie reputation
*at execute() time*.

OpenSesame owns the half it can drive from the page it is handed:

1. **Warm** the live session before minting — human-like fuzzy mouse motion plus
   the occasional trusted click (no scrolling: it added latency without moving the
   score). v3 reads interaction signals, and the token is minted *after* the warm
   so it reflects it.
2. **Mint** cleanly via ``grecaptcha(.enterprise).execute(sitekey, {action})`` and
   hand back the token.

The dominant half — a clean/aged **IP + cookie reputation**, then **headful**
Chrome — is the *launch owner's* job (the engine never owns the browser), exactly
as for Turnstile's managed challenge. Measured on 2captcha's demos the score is
volatile and reputation-bound (the same key swung 0.1↔0.9 minutes apart; bursty
minting penalises the IP). A token always mints, so the engine returns SOLVED with
it and records what it knows in metadata; the caller treats a persistently low
*score* as the anti-bot track (it is the one observing the score, not the engine).

The engine auto-discovers everything it needs from the live page (the grecaptcha
script's ``render=`` sitekey, whether ``grecaptcha.enterprise`` exists, and a
``data-action`` on the page), so it needs no per-site config.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    TokenSolution,
)

# Discover sitekey + enterprise flag + action from the live page. The v3 sitekey
# is the ``render=`` param of the recaptcha script (``api.js`` standard /
# ``enterprise.js`` enterprise); ``render`` is sometimes ``explicit``/``onload``
# (no key) — then we fall back to the grecaptcha client config. The action is the
# site's own ``data-action`` if present.
DISCOVER_JS = r"""
(() => {
  let sitekey = null, enterprise = false;
  for (const s of document.querySelectorAll('script[src]')) {
    const m = s.src.match(/recaptcha\/(enterprise|api)\.js\?[^"']*\brender=([A-Za-z0-9_-]+)/);
    if (m) { enterprise = (m[1] === 'enterprise'); sitekey = m[2]; break; }
  }
  if (sitekey === 'explicit' || sitekey === 'onload') sitekey = null;
  enterprise = enterprise || !!(window.grecaptcha && window.grecaptcha.enterprise);
  if (!sitekey && window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
    try {
      const k = Object.keys(window.___grecaptcha_cfg.clients)[0];
      const sk = window.___grecaptcha_cfg.clients[k] && window.___grecaptcha_cfg.clients[k].sitekey;
      if (sk) sitekey = sk;
    } catch (e) {}
  }
  const el = document.querySelector('[data-action]');
  const action = el ? el.getAttribute('data-action') : null;
  // A v2 widget anchors a checkbox iframe (api2/anchor) or renders a .g-recaptcha
  // div; v3 has neither — it only loads the render script. This tells the two apart.
  const widget = !!document.querySelector(
    'iframe[src*="api2/anchor"], iframe[src*="enterprise/anchor"], '
    + '.g-recaptcha[data-sitekey]:not([data-size="invisible"])');
  const g = window.grecaptcha;
  const ready = typeof g !== 'undefined' && (enterprise ? !!g.enterprise : true);
  return { sitekey, enterprise, action, widget, ready };
})()
"""


def is_recaptcha_v3(info: Any) -> bool:
    """Classify a discovery probe as reCAPTCHA v3.

    v3 is the score-only variant: a ``render=`` sitekey script is present and there
    is **no** v2 checkbox widget/anchor frame. Pure + side-effect-free so it can be
    unit-tested against a captured ``DISCOVER_JS`` result without a live browser.
    """

    if not isinstance(info, dict):
        return False
    return bool(info.get("sitekey")) and not info.get("widget")


def _mint_js(sitekey: str, enterprise: bool, action: str) -> str:
    ns = "grecaptcha.enterprise" if enterprise else "grecaptcha"
    return f"""
    (async () => {{
      try {{
        const g = {ns};
        await new Promise((res) => g.ready(res));
        const t = await g.execute({json.dumps(sitekey)}, {{action: {json.dumps(action)}}});
        return {{ ok: true, token: t }};
      }} catch (e) {{ return {{ ok: false, err: String(e) }}; }}
    }})()
    """


def _bezier(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    )


class RecaptchaV3Engine:
    """reCAPTCHA v3: warm the session for trust, then mint a scored token."""

    family = Family.RECAPTCHA_V3

    def __init__(
        self,
        *,
        warm_s: float = 6.0,
        viewport: tuple[int, int] = (1180, 820),
        default_action: str = "verify",
        ready_tries: int = 20,
        seed: int | None = None,
    ) -> None:
        self.warm_s = warm_s
        self.viewport = viewport
        self.default_action = default_action
        self.ready_tries = ready_tries
        self.seed = seed

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        return []  # no local model — v3 is behavioural/reputation, not a puzzle

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        info = await self._discover(page)
        sitekey = challenge.sitekey or info.get("sitekey")
        if not sitekey:
            return self._fail(challenge, "no reCAPTCHA v3 sitekey found on page "
                                         "(no grecaptcha render script / client config)")
        enterprise = bool(info.get("enterprise"))
        # Track where the action came from: a v3 token minted with the wrong action
        # still scores, but the site's server-side check rejects an action mismatch,
        # so a *guessed* default is a real caveat the caller must see.
        if challenge.action:
            action, action_source = challenge.action, "challenge"
        elif info.get("action"):
            action, action_source = str(info["action"]), "page"
        else:
            action, action_source = self.default_action, "default"

        # Per-policy warm override (models["recaptcha_v3_warm_s"] = "0" to skip).
        warm_s = self._warm_s(policy)
        if warm_s > 0:
            await self._warm(page, warm_s, self._warm_rng(correlation_id))

        mint = await page.eval_js(_mint_js(str(sitekey), enterprise, str(action)))
        if not isinstance(mint, dict) or not mint.get("ok") or not mint.get("token"):
            err = (mint or {}).get("err", "grecaptcha.execute returned no token") if isinstance(mint, dict) else "mint failed"
            return self._fail(challenge, f"v3 token mint failed: {err}",
                              sitekey=str(sitekey), enterprise=enterprise, action=str(action))

        return SolveResult(
            status=SolveStatus.SOLVED, family=Family.RECAPTCHA_V3,
            solution=TokenSolution(str(mint["token"])), solved_by=SolvedBy.LOCAL,
            vendor=challenge.vendor_kind or ("recaptcha_enterprise" if enterprise else "recaptcha"),
            metadata={
                "strategy": "recaptcha-v3",
                "enterprise": enterprise,
                "sitekey": str(sitekey),
                "action": str(action),
                "action_source": action_source,   # "challenge" | "page" | "default" (guessed)
                "warmed_s": round(warm_s, 1),
                # The score is a server-side, environment-decided value the engine
                # cannot observe; the launch owner controls headful + IP/profile.
                "note": "v3 token minted; score is environment-trust-decided server-side",
            },
        )

    # -- internals --------------------------------------------------------

    def _warm_rng(self, correlation_id: str | None) -> random.Random:
        """RNG for the warm trajectory. A fixed ``seed`` gives reproducible paths
        (tests/debug); the default (``seed=None``) draws fresh OS entropy per solve
        so the pointer trajectory + click cadence are NOT a replayable fingerprint
        Google can cluster on. A ``correlation_id``, when present, seeds
        deterministically so a single solve can be reproduced from its id."""

        if self.seed is not None:
            return random.Random(self.seed)
        if correlation_id:
            return random.Random(correlation_id)
        return random.Random()

    def _warm_s(self, policy: SolverPolicy) -> float:
        raw = policy.models.get("recaptcha_v3_warm_s")
        if raw is None:
            return self.warm_s
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return self.warm_s

    async def _discover(self, page: Any) -> dict[str, Any]:
        for _ in range(self.ready_tries):
            try:
                info = await page.eval_js(DISCOVER_JS)
            except Exception:
                info = None
            if isinstance(info, dict) and info.get("ready") and info.get("sitekey"):
                return info
            await asyncio.sleep(0.5)
        return info if isinstance(info, dict) else {}

    async def _warm(self, page: Any, seconds: float, rng: random.Random) -> None:
        """Human-like fuzzy mouse motion + the occasional benign click — the
        interaction signals v3 reads. No scrolling: the score sweep showed
        scroll/dwell add latency without moving the number. The dominant lever is
        out of the engine's hands (headful + a clean/aged IP); among in-page signals
        natural pointer movement and a real trusted click are the cheap, plausible
        contributors. ``rng`` is per-solve (see :meth:`_warm_rng`) so the path is not
        a fixed signature. Every CDP op is guarded so a thin page can't abort the mint."""

        w, h = self.viewport
        x, y = rng.uniform(60, w - 60), rng.uniform(60, h - 60)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            tx, ty = rng.uniform(40, w - 40), rng.uniform(80, h - 80)
            c1 = (x + rng.uniform(-180, 180), y + rng.uniform(-140, 140))
            c2 = (tx + rng.uniform(-180, 180), ty + rng.uniform(-140, 140))
            steps = rng.randint(16, 28)
            for i in range(steps + 1):
                t = i / steps
                ease = 0.5 - 0.5 * math.cos(math.pi * t)  # ease-in-out
                px, py = _bezier((x, y), c1, c2, (tx, ty), ease)
                await self._move(page, px + rng.uniform(-1.2, 1.2), py + rng.uniform(-1.2, 1.2))
                await asyncio.sleep(rng.uniform(0.008, 0.022))
            x, y = tx, ty
            if rng.random() < 0.35:                       # an occasional human-like click
                await self._click(page, x, y, rng)
            await asyncio.sleep(rng.uniform(0.1, 0.35))   # brief human pause between moves

    async def _click(self, page: Any, x: float, y: float, rng: random.Random) -> None:
        """A trusted compositor press/release at the current pointer — a real click
        interaction signal, on empty page chrome so it changes nothing. Best-effort."""

        dme = getattr(page, "dispatch_mouse_event", None)
        if not callable(dme):
            return
        try:
            await dme("mousePressed", x, y, button="left", click_count=1)
            await asyncio.sleep(rng.uniform(0.04, 0.11))  # human press-hold
            await dme("mouseReleased", x, y, button="left", click_count=1)
        except Exception:
            pass

    async def _move(self, page: Any, x: float, y: float) -> None:
        try:
            await page.dispatch_mouse_event("mouseMoved", x, y)
        except Exception:
            mover = getattr(page, "move_mouse", None)
            if callable(mover):
                try:
                    await mover(x, y)
                except Exception:
                    pass

    def _fail(self, challenge, error: str, **md: Any) -> SolveResult:
        metadata: dict[str, Any] = {"strategy": "recaptcha-v3"}
        metadata.update(md)
        return SolveResult(
            status=SolveStatus.FAILED, family=Family.RECAPTCHA_V3, error=error, metadata=metadata,
        )
