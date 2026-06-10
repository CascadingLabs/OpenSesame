"""reCAPTCHA v2 audio side-door — local Whisper STT, driven via the DOM.

Proven path: switch to the audio challenge, read the signed MP3 URL from the
same-origin bframe DOM, download it, transcribe with a local model, type the
answer, verify, harvest the token. All interaction is **DOM-level** (read the
contentDocument, set values, click elements) — never pixel coordinates — and the
answer is typed via VoidCrawl's input actions. Model inference is delegated to a
registry ``Transcriber``.

NOTE: same-origin DOM access works on Google's own ``api2/demo``. On a real
third-party site the bframe is cross-origin; that variant downloads the audio
through the page's network context instead (future engine variant).
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import urllib.request
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

DEFAULT_MODEL = "openai/whisper-base.en"
PROVIDER_KIND = "whisper"

# Open the challenge by DOM-clicking the anchor checkbox (if not already open).
_OPEN_CHALLENGE = with_selectors(r"""
(() => {
  const f = document.querySelector('__BFRAME_SEL__');
  if (f && f.contentDocument
      && f.contentDocument.querySelector('#recaptcha-audio-button, #rc-imageselect')) {
    return 'open';
  }
  const a = document.querySelector('__ANCHOR_SEL__');
  const cb = a && a.contentDocument && a.contentDocument.querySelector('#recaptcha-anchor');
  if (cb) { cb.click(); return 'clicked'; }
  return 'no-anchor';
})()
""")

# Switch to the audio challenge.
_CLICK_AUDIO_BUTTON = with_selectors(r"""
(() => {
  const f = document.querySelector('__BFRAME_SEL__');
  const b = f && f.contentDocument && f.contentDocument.querySelector('#recaptcha-audio-button');
  if (b) { b.click(); return true; }
  return false;
})()
""")

# Read the audio-challenge DOM state (signed MP3 url, rate-limit, token).
_AUDIO_STATE = with_selectors(r"""
(() => {
  const f = document.querySelector('__BFRAME_SEL__');
  if (!f) return {ok:false, reason:'no-frame'};
  if (!f.contentDocument) return {ok:false, reason:'cross-origin'};
  const doc = f.contentDocument;
  const dl = doc.querySelector('.rc-audiochallenge-tdownload-link');
  const src = doc.querySelector('#audio-source');
  const resp = doc.querySelector('#audio-response');
  const blocked = doc.querySelector('.rc-doscaptcha-header, .rc-doscaptcha-body');
  const tok = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
  return {
    ok: true,
    download: dl ? dl.href : (src ? src.src : null),
    has_response: !!resp,
    rate_limited: !!blocked,
    token: tok ? (tok.value || '') : '',
  };
})()
""")

# Fill the response field at the DOM level (set value + fire input/change).
_SET_RESPONSE = with_selectors(r"""
(() => {
  const f = document.querySelector('__BFRAME_SEL__');
  const el = f && f.contentDocument && f.contentDocument.querySelector('#audio-response');
  if (!el) return false;
  el.focus();
  el.value = __ANSWER__;
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return true;
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

_READ_TOKEN = (
    "document.querySelector('#g-recaptcha-response, textarea[name=\"g-recaptcha-response\"]')"
    "?.value || ''"
)


class RecaptchaAudioEngine:
    """Solves a reCAPTCHA v2 challenge through its audio side-door."""

    family = Family.RECAPTCHA_V2

    def __init__(self, *, default_model: str = DEFAULT_MODEL, max_attempts: int = 4) -> None:
        self.default_model = default_model
        self.max_attempts = max_attempts

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        model_id = policy.models.get("recaptcha_v2_audio") or self.default_model
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
        transcriber = registry.get(key)  # load-once, cached for the process
        return await self._solve(challenge, page, transcriber, key.model_id, policy.device)

    async def _solve(self, challenge, page, transcriber, model_id, device) -> SolveResult:
        token = str(await page.eval_js(_READ_TOKEN) or "")
        if token:
            return self._ok(challenge, token, model_id, device)

        await self._open_challenge(page)         # DOM-click the checkbox if needed
        token = str(await page.eval_js(_READ_TOKEN) or "")
        if token:                                 # checkbox auto-passed, no challenge
            return self._ok(challenge, token, model_id, device)

        await page.eval_js(_CLICK_AUDIO_BUTTON)
        await asyncio.sleep(1.0)

        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(self.max_attempts):
                state = await self._wait_for_audio(page)
                if not isinstance(state, dict) or not state.get("ok"):
                    return unreadable_result(challenge, state, strategy="audio")
                if state.get("rate_limited"):
                    return self._rate_limited(challenge)
                download = state.get("download")
                if not download:
                    return self._fail(challenge, "no audio download url")

                mp3 = Path(tmp) / "audio.mp3"
                await asyncio.to_thread(_download, download, mp3)
                text = normalize_answer(await asyncio.to_thread(transcriber.transcribe, str(mp3)))

                await self._set_response(page, text)
                await type_via_actions(page, text)  # VoidCrawl input action (best-effort)
                await page.eval_js(_CLICK_VERIFY)

                token = await self._read_token_after(page)
                if token:
                    return self._ok(challenge, token, model_id, device, transcript=text)
                await asyncio.sleep(1.0)

        return self._fail(challenge, "no token after audio attempts")

    async def _open_challenge(self, page, *, tries: int = 12) -> None:
        if await page.eval_js(_OPEN_CHALLENGE) == "open":
            return
        for _ in range(tries):  # wait for the challenge frame to render
            ready = await page.eval_js(with_selectors(
                "(() => { const f = document.querySelector('__BFRAME_SEL__');"
                " return !!(f && f.contentDocument"
                " && f.contentDocument.querySelector('#recaptcha-audio-button')); })()"
            ))
            if ready:
                return
            await asyncio.sleep(0.5)

    async def _wait_for_audio(self, page, *, tries: int = 16) -> Any:
        for _ in range(tries):
            state = await page.eval_js(_AUDIO_STATE)
            if isinstance(state, dict) and state.get("ok") and (
                state.get("download") or state.get("rate_limited")
            ):
                return state
            await asyncio.sleep(0.4)
        return await page.eval_js(_AUDIO_STATE)

    async def _set_response(self, page, text: str) -> None:
        import json

        await page.eval_js(_SET_RESPONSE.replace("__ANSWER__", json.dumps(text)))

    async def _read_token_after(self, page, *, tries: int = 12) -> str:
        for _ in range(tries):
            token = str(await page.eval_js(_READ_TOKEN) or "")
            if token:
                return token
            await asyncio.sleep(0.5)
        return ""

    def _ok(self, challenge, token, model_id, device, *, transcript: str = "") -> SolveResult:
        return SolveResult(
            status=SolveStatus.SOLVED, family=challenge.family,
            solution=TokenSolution(token), solved_by=SolvedBy.LOCAL,
            vendor=challenge.vendor_kind, model_id=model_id, device=device,
            metadata={"strategy": "audio", "transcript": transcript},
        )

    def _fail(self, challenge, error: str) -> SolveResult:
        return SolveResult(status=SolveStatus.FAILED, family=challenge.family, error=error,
                           metadata={"strategy": "audio"})

    def _rate_limited(self, challenge) -> SolveResult:
        return SolveResult(status=SolveStatus.RATE_LIMITED, family=challenge.family,
                           error="reCAPTCHA audio rate-limited", metadata={"strategy": "audio"})


def normalize_answer(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", text or "").lower()
    return " ".join(cleaned.split())


def _download(url: str, out: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (signed same-host url)
        out.write_bytes(resp.read())


async def type_via_actions(page: Any, text: str) -> None:
    """Type via VoidCrawl's CdpTypeText action into the focused field (best-effort).

    The response field was focused by the DOM set step; CdpTypeText drives real
    CDP key events into the focused element. If voidcrawl.actions is unavailable
    (API-only env / tests), the DOM set above already populated the value.
    """

    try:
        from voidcrawl.actions import Flow
        from voidcrawl.actions.builtin.input import CdpTypeText
    except Exception:
        return
    try:
        await Flow([CdpTypeText(text)]).run(page)
    except Exception:
        pass
