"""reCAPTCHA v2 image grid — local tile classifier, driven via the DOM.

DOM-first: read the same-origin bframe to get the instructions, exact grid shape
(``rc-imageselect-table-NN``), each tile's bounding rect, and the grid bbox; crop
the grid precisely, ask a registry ``TileSelector`` which tiles match the target,
then **click those tile elements via the DOM** — not pixel coordinates. Verify,
harvest the token. Dynamic ("click verify once there are none left") challenges
loop until a round selects nothing new.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines._recaptcha_dom import unreadable_result, with_selectors
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    TokenSolution,
)

DEFAULT_MODEL = "verytuffcat/recaptcha"
PROVIDER_KIND = "tiles"

# Structured grid state from the same-origin bframe DOM.
_GRID_STATE = with_selectors(r"""
(() => {
  const f = document.querySelector('__BFRAME_SEL__');
  if (!f) return {ok:false, reason:'no-frame'};
  if (!f.contentDocument) return {ok:false, reason:'cross-origin'};
  const doc = f.contentDocument;
  const tok = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
  const token = tok ? (tok.value || '') : '';
  const instr = doc.querySelector('.rc-imageselect-instructions');
  const table = doc.querySelector('#rc-imageselect-target table');
  if (!table) return {ok:true, present:false, token};
  const cls = table.className || '';
  const m = cls.match(/rc-imageselect-table-(\d)(\d)/);
  const rows = m ? parseInt(m[1],10) : 3, cols = m ? parseInt(m[2],10) : 3;
  const fr = f.getBoundingClientRect();
  const tds = Array.from(table.querySelectorAll('td'));
  let left=1e9, top=1e9, right=0, bottom=0;
  const cells = tds.map((td, i) => {
    const r = td.getBoundingClientRect();
    const x = fr.left + r.left, y = fr.top + r.top;
    left=Math.min(left,x); top=Math.min(top,y);
    right=Math.max(right,x+r.width); bottom=Math.max(bottom,y+r.height);
    const sel = td.getAttribute('aria-pressed')==='true'
      || td.className.indexOf('selected')!==-1;   // tileselected (one-shot) or dynamic-selected
    return {index:i, row:Math.floor(i/cols), col:i%cols, selected:!!sel};
  });
  return {
    ok:true, present:true, token,
    instructions: instr ? instr.innerText.replace(/\s+/g,' ').trim() : '',
    rows, cols, cells,
    grid: {x:left, y:top, width:right-left, height:bottom-top},
  };
})()
""")

# Click the td at a given flat index via the DOM.
_CLICK_CELL = with_selectors(r"""
(() => {
  const f = document.querySelector('__BFRAME_SEL__');
  const tds = f && f.contentDocument
    && f.contentDocument.querySelectorAll('#rc-imageselect-target table td');
  if (!tds || !tds[__I__]) return false;
  tds[__I__].click(); return true;
})()
""")

_CLICK_VERIFY = with_selectors(r"""
(() => {
  const f = document.querySelector('__BFRAME_SEL__');
  const el = f && f.contentDocument && f.contentDocument.querySelector('#recaptcha-verify-button');
  if (!el) return false;
  el.click(); return true;
})()
""")


class RecaptchaGridEngine:
    """Solves a reCAPTCHA v2 image-grid challenge via DOM tile clicks."""

    family = Family.RECAPTCHA_V2

    def __init__(self, *, default_model: str = DEFAULT_MODEL, max_rounds: int = 6) -> None:
        self.default_model = default_model
        self.max_rounds = max_rounds

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        model_id = policy.models.get("recaptcha_v2_grid") or self.default_model
        return [ModelKey(kind=PROVIDER_KIND, model_id=model_id, device=policy.device)]

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        key = self.model_keys(policy)[0]
        selector = registry.get(key)  # load-once, cached for the process
        return await self._solve(challenge, page, selector, key.model_id, policy.device)

    async def _open_challenge(self, page, *, tries: int = 12) -> None:
        """DOM-click the anchor checkbox to open the image challenge, if needed."""

        already = await page.eval_js(with_selectors(
            "(() => { const f = document.querySelector('__BFRAME_SEL__');"
            " return !!(f && f.contentDocument"
            " && f.contentDocument.querySelector('#rc-imageselect')); })()"
        ))
        if already:
            return
        await page.eval_js(with_selectors(
            "(() => { const a = document.querySelector('__ANCHOR_SEL__');"
            " const cb = a && a.contentDocument && a.contentDocument.querySelector('#recaptcha-anchor');"
            " if (cb) cb.click(); })()"
        ))
        for _ in range(tries):
            ready = await page.eval_js(with_selectors(
                "(() => { const f = document.querySelector('__BFRAME_SEL__');"
                " return !!(f && f.contentDocument"
                " && f.contentDocument.querySelector('#rc-imageselect-target table')); })()"
            ))
            if ready:
                return
            await asyncio.sleep(0.5)

    async def _solve(self, challenge, page, selector, model_id, device) -> SolveResult:
        await self._open_challenge(page)
        with tempfile.TemporaryDirectory() as tmp:
            for round_index in range(self.max_rounds):
                state = await page.eval_js(_GRID_STATE)
                if not isinstance(state, dict) or not state.get("ok"):
                    return unreadable_result(challenge, state, strategy="grid")
                if state.get("token"):
                    return self._ok(challenge, state["token"], model_id, device)
                if not state.get("present"):
                    break

                target = parse_target(state.get("instructions", ""))
                if not target:
                    return self._fail(challenge, f"no target parsed: {state.get('instructions')!r}")
                rows, cols = int(state["rows"]), int(state["cols"])
                grid = state["grid"]
                image = Path(tmp) / f"grid-{round_index}.png"
                await page.screenshot(
                    path=str(image),
                    bbox=(int(grid["x"]), int(grid["y"]), int(grid["width"]), int(grid["height"])),
                )

                picks = await asyncio.to_thread(
                    selector.select_tiles, str(image), rows=rows, cols=cols, target=target,
                )
                selected = {(c["row"], c["col"]) for c in state["cells"] if c.get("selected")}
                clicked = 0
                for row, col, _score in picks:
                    if (row, col) in selected:
                        continue
                    if await page.eval_js(_CLICK_CELL.replace("__I__", str(row * cols + col))):
                        clicked += 1
                if clicked == 0:
                    break
                await asyncio.sleep(1.0)  # let dynamic tiles settle before verify/next round

            await page.eval_js(_CLICK_VERIFY)
            token = await self._read_token_after(page)
            if token:
                return self._ok(challenge, token, model_id, device)
        return self._fail(challenge, "no token after grid rounds")

    async def _read_token_after(self, page, *, tries: int = 12) -> str:
        read = (
            "document.querySelector('#g-recaptcha-response, "
            "textarea[name=\"g-recaptcha-response\"]')?.value || ''"
        )
        for _ in range(tries):
            token = str(await page.eval_js(read) or "")
            if token:
                return token
            await asyncio.sleep(0.5)
        return ""

    def _ok(self, challenge, token, model_id, device) -> SolveResult:
        return SolveResult(
            status=SolveStatus.SOLVED, family=challenge.family,
            solution=TokenSolution(token), solved_by=SolvedBy.LOCAL,
            vendor=challenge.vendor_kind, model_id=model_id, device=device,
            metadata={"strategy": "grid"},
        )

    def _fail(self, challenge, error: str) -> SolveResult:
        return SolveResult(status=SolveStatus.FAILED, family=challenge.family, error=error,
                           metadata={"strategy": "grid"})


def parse_target(instructions: str) -> str | None:
    text = re.sub(r"\s+", " ", (instructions or "").strip().lower())
    for pat in (
        r"select all (?:images|squares) with (?:a )?([a-z ]+?)(?: if| click|$)",
        r"select all ([a-z ]+?)(?: if| click|$)",
    ):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return None
