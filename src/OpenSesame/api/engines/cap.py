"""Cap (@cap.js) proof-of-work captcha engine.

Cap is computational, not perceptual: the widget fetches a challenge descriptor
``{c, s, d}`` + a ``token``, derives ``c`` ``(salt, target)`` subchallenges from
the token, and finds a ``nonce`` for each whose ``sha256(salt+nonce)`` hex starts
with ``target``. OpenSesame does the same work in Python (validated against a live
``@cap.js/server``) and redeems the nonces for a ``cap-token``, which it injects
into the page so the form submits.

The Cap challenge/redeem endpoints are plain HTTP, so the solve is server-side;
the live page is touched only to read the widget's ``data-cap-api-endpoint`` and
to drop the minted token in. Endpoints with Cap's optional *instrumentation*
anti-bot layer enabled are reported as a ``route`` (that layer is browser-side,
beyond the proof-of-work).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines._pow import cap_solve
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    Timing,
    TokenSolution,
)

READ_WIDGET_JS = r"""(() => {
  const w = document.querySelector('cap-widget, [data-cap-api-endpoint]');
  if (!w) return null;
  const ep = w.getAttribute('data-cap-api-endpoint');
  const field = w.querySelector('input[name]') || document.querySelector('input[name="cap-token"]');
  return { endpoint: ep, fieldName: field ? field.getAttribute('name') : 'cap-token' };
})()"""


class CapEngine:
    """Cap proof-of-work: derive → solve nonces → redeem → inject ``cap-token``."""

    family = Family.CAP

    def __init__(self, *, http_timeout: float = 30.0) -> None:
        self.http_timeout = http_timeout

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        return []

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        started = time.time()

        # 1. Locate the widget's API endpoint + token field (from the page, or metadata).
        endpoint = challenge.metadata.get("cap_api_endpoint")
        field_name = challenge.metadata.get("cap_field", "cap-token")
        if endpoint is None and page is not None:
            info = await page.eval_js(READ_WIDGET_JS)
            if isinstance(info, dict):
                endpoint = info.get("endpoint")
                field_name = info.get("fieldName") or field_name
        if not endpoint:
            return self._fail(started, "no cap-widget / data-cap-api-endpoint found")

        ep = endpoint if endpoint.endswith("/") else endpoint + "/"

        # 2. Fetch the challenge, solve the proof-of-work, redeem for a token.
        try:
            token, cap_token, meta = await self._mint(ep)
        except Exception as exc:  # surfaced as a value
            return self._fail(started, f"{type(exc).__name__}: {exc}")
        if cap_token is None:
            return self._fail(started, meta.get("error", "redeem rejected"), route=meta.get("route"),
                              extra=meta)

        # 3. Drop the token into the page so the host form accepts it.
        applied = False
        if page is not None:
            applied = bool(await page.eval_js(_inject_js(field_name, cap_token)))

        return SolveResult(
            status=SolveStatus.SOLVED, family=Family.CAP,
            solution=TokenSolution(cap_token), solved_by=SolvedBy.LOCAL, vendor="cap",
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "cap-pow", "applied_to_page": applied, **meta},
        )

    async def _mint(self, ep: str) -> tuple[str, str | None, dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.http_timeout) as client:
            ch = (await client.post(f"{ep}challenge", json={})).json()
            cfg = ch["challenge"]
            token = ch["token"]
            instrumented = "instrumentation" in ch
            t0 = time.time()
            solutions = cap_solve(token, cfg["c"], cfg["s"], cfg["d"])
            solve_ms = (time.time() - t0) * 1000.0
            meta: dict[str, Any] = {
                "subchallenges": cfg["c"], "difficulty": cfg["d"],
                "pow_solve_ms": round(solve_ms, 1), "instrumented": instrumented,
            }
            r = await client.post(f"{ep}redeem", json={"token": token, "solutions": solutions})
            resp = r.json()
            if resp.get("success") and resp.get("token"):
                return token, resp["token"], meta
            # PoW was computed; the endpoint declined to mint (e.g. instrumentation gate).
            if resp.get("instr_error") or "instrumentation" in str(resp).lower():
                meta["route"] = "instrumentation"
            meta["error"] = resp.get("error") or resp.get("reason") or str(resp)[:160]
            return token, None, meta

    def _fail(self, started, error, *, route=None, extra=None) -> SolveResult:
        md: dict[str, Any] = {"strategy": "cap-pow", **(extra or {})}
        if route:
            md["route"] = route
        return SolveResult(
            status=SolveStatus.FAILED, family=Family.CAP, error=error,
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata=md,
        )


def _inject_js(field_name: str, token: str) -> str:
    import json

    args = json.dumps({"f": field_name, "v": token})
    return (
        "(() => { const t = " + args + "; "
        "let el = document.querySelector('input[name=\"' + t.f + '\"]'); "
        "if (!el) { const w = document.querySelector('cap-widget'); el = w && w.querySelector('input'); } "
        "if (el) { el.value = t.v; el.dispatchEvent(new Event('input', {bubbles:true})); "
        "el.dispatchEvent(new Event('change', {bubbles:true})); } "
        "const w = document.querySelector('cap-widget'); "
        "if (w) { try { w.dispatchEvent(new CustomEvent('solve', {detail:{token:t.v}})); } catch (e) {} } "
        "return !!el; })()"
    )
