"""ALTCHA proof-of-work captcha engine.

ALTCHA hands the client a challenge ``{algorithm, challenge, salt, maxnumber,
signature}`` and asks for the ``number`` in ``[0, maxnumber]`` whose
``sha256(salt+number)`` hex equals ``challenge``. OpenSesame brute-forces it (no
model, no human), builds the base64 solution payload the widget would, and drops
it into the page's ``altcha`` field so the form submits.

The challenge comes from the widget's inline ``challengejson`` or its
``challengeurl`` (fetched in-page so the host session/cookies apply).
"""

from __future__ import annotations

import json
import time
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines._pow import altcha_payload, altcha_solve
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
  const w = document.querySelector('altcha-widget, [data-altcha], .altcha');
  if (!w) return null;
  return {
    challengejson: w.getAttribute('challengejson') || w.getAttribute('data-challengejson'),
    challengeurl: w.getAttribute('challengeurl') || w.getAttribute('data-challengeurl'),
    name: w.getAttribute('name') || 'altcha',
  };
})()"""


class AltchaEngine:
    """ALTCHA proof-of-work: read challenge → brute-force number → inject payload."""

    family = Family.ALTCHA

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

        chal = challenge.metadata.get("altcha_challenge")
        field = challenge.metadata.get("altcha_field", "altcha")
        if chal is None and page is not None:
            info = await page.eval_js(READ_WIDGET_JS)
            if isinstance(info, dict):
                field = info.get("name") or field
                if info.get("challengejson"):
                    chal = json.loads(info["challengejson"])
                elif info.get("challengeurl"):
                    chal = await self._fetch(page, info["challengeurl"])
        if not isinstance(chal, dict) or "challenge" not in chal:
            return self._fail(started, "no ALTCHA challenge found on the page")

        number = altcha_solve(
            chal["salt"], chal["challenge"], chal.get("maxnumber", 1_000_000),
            chal.get("algorithm", "SHA-256"),
        )
        if number is None:
            return self._fail(started, "no solution within maxnumber (challenge unsolvable)")

        payload = altcha_payload(chal, number, (time.time() - started) * 1000.0)

        applied = False
        if page is not None:
            applied = bool(await page.eval_js(_inject_js(field, payload)))

        return SolveResult(
            status=SolveStatus.SOLVED, family=Family.ALTCHA,
            solution=TokenSolution(payload), solved_by=SolvedBy.LOCAL, vendor="altcha",
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "altcha-pow", "number": number,
                      "maxnumber": chal.get("maxnumber"), "applied_to_page": applied},
        )

    async def _fetch(self, page: Any, url: str) -> Any:
        return await page.eval_js(
            "(async () => { const r = await fetch(" + json.dumps(url)
            + ", {credentials:'same-origin'}); return await r.json(); })()"
        )

    def _fail(self, started, error) -> SolveResult:
        return SolveResult(
            status=SolveStatus.FAILED, family=Family.ALTCHA, error=error,
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "altcha-pow"},
        )


def _inject_js(field_name: str, payload: str) -> str:
    args = json.dumps({"f": field_name, "v": payload})
    return (
        "(() => { const t = " + args + "; "
        "let el = document.querySelector('input[name=\"' + t.f + '\"]'); "
        "if (!el) { const w = document.querySelector('altcha-widget'); el = w && w.querySelector('input[name]'); } "
        "if (el) { el.value = t.v; el.dispatchEvent(new Event('input', {bubbles:true})); "
        "el.dispatchEvent(new Event('change', {bubbles:true})); } "
        "const w = document.querySelector('altcha-widget'); "
        "if (w) { try { w.dispatchEvent(new CustomEvent('verified', {detail:{payload:t.v}})); } catch (e) {} } "
        "return !!el; })()"
    )
