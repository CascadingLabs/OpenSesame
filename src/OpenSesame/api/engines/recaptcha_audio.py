"""reCAPTCHA v2 audio side-door — local Whisper STT, driven via frame-scoped eval.

Proven path: switch to the audio challenge, read the signed MP3 URL from the
challenge frame, download it, transcribe with a local model, type the answer,
verify, harvest the token. All frame interaction goes through
``page.eval_js_in_frame`` (VoidCrawl 0.3.5), so the same code drives the
challenge whether its ``bframe`` is same-origin (``api2/demo``) or **cross-origin**
(a real third-party site) — the inner JS runs in the frame's own context, where
``document`` is the challenge document and the signed audio URL is readable.
Interaction stays DOM-level (read state, set the response value, click elements);
the answer is also typed via VoidCrawl's CDP input actions. Model inference is
delegated to a registry ``Transcriber``.

Cross-origin requires the session launched with
``extra_args=["disable-site-isolation-trials"]`` so Chrome keeps the google.com
frames in-process; otherwise the frame is unreachable and the engine returns an
actionable error.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines._recaptcha_dom import (
    ANCHOR_PATTERNS,
    BFRAME_PATTERNS,
    FrameAccess,
    FrameUnreachable,
    frame_unreachable_result,
)
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

# Is the challenge already open? — runs inside the bframe.
_CHALLENGE_OPEN_FRAME = "(() => !!document.querySelector('#recaptcha-audio-button, #rc-imageselect'))()"
_AUDIO_BUTTON_READY_FRAME = "(() => !!document.querySelector('#recaptcha-audio-button'))()"
# Click the checkbox — runs inside the anchor frame.
_CLICK_ANCHOR_FRAME = (
    "(() => { const cb = document.querySelector('#recaptcha-anchor');"
    " if (cb) { cb.click(); return true; } return false; })()"
)

# Switch to the audio challenge — runs inside the bframe.
_CLICK_AUDIO_BUTTON_FRAME = (
    "(() => { const b = document.querySelector('#recaptcha-audio-button');"
    " if (b) { b.click(); return true; } return false; })()"
)

# Audio-challenge state (signed MP3 url, rate-limit) — runs inside the bframe.
_AUDIO_STATE_FRAME = r"""
(() => {
  const dl = document.querySelector('.rc-audiochallenge-tdownload-link');
  const src = document.querySelector('#audio-source');
  const resp = document.querySelector('#audio-response');
  const blocked = document.querySelector('.rc-doscaptcha-header, .rc-doscaptcha-body');
  return {
    download: dl ? dl.href : (src ? src.src : null),
    has_response: !!resp,
    rate_limited: !!blocked,
  };
})()
"""

# Fill the response field (set value + fire input/change) — runs inside the bframe.
_SET_RESPONSE_FRAME = r"""
(() => {
  const el = document.querySelector('#audio-response');
  if (!el) return false;
  el.focus();
  el.value = __ANSWER__;
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return true;
})()
"""

_CLICK_VERIFY_FRAME = (
    "(() => { const el = document.querySelector('#recaptcha-verify-button');"
    " if (!el) return false; el.click(); return true; })()"
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
        return await self._solve(challenge, FrameAccess(page), page, transcriber, key.model_id, policy.device)

    async def _solve(self, challenge, frames, page, transcriber, model_id, device) -> SolveResult:
        token = await frames.token()
        if token:
            return self._ok(challenge, token, model_id, device)

        await self._open_challenge(frames)       # click the checkbox if needed
        token = await frames.token()
        if token:                                 # checkbox auto-passed, no challenge
            return self._ok(challenge, token, model_id, device)

        try:
            await frames.eval_frame(BFRAME_PATTERNS, _CLICK_AUDIO_BUTTON_FRAME)
        except FrameUnreachable as exc:
            return frame_unreachable_result(challenge, exc, strategy="audio")
        await asyncio.sleep(1.0)

        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(self.max_attempts):
                try:
                    state = await self._wait_for_audio(frames)
                except FrameUnreachable as exc:
                    return frame_unreachable_result(challenge, exc, strategy="audio")
                if not isinstance(state, dict):
                    return self._fail(challenge, "audio state unreadable")
                if state.get("rate_limited"):
                    return self._rate_limited(challenge)
                download = state.get("download")
                if not download:
                    return self._fail(challenge, "no audio download url")

                mp3 = Path(tmp) / "audio.mp3"
                await asyncio.to_thread(_download, download, mp3)
                text = normalize_answer(await asyncio.to_thread(transcriber.transcribe, str(mp3)))

                await self._set_response(frames, text)
                await type_via_actions(page, text)  # VoidCrawl input action (best-effort)
                await frames.eval_frame(BFRAME_PATTERNS, _CLICK_VERIFY_FRAME)

                token = await self._read_token_after(frames)
                if token:
                    return self._ok(challenge, token, model_id, device, transcript=text)
                await asyncio.sleep(1.0)

        return self._fail(challenge, "no token after audio attempts")

    async def _open_challenge(self, frames: FrameAccess, *, tries: int = 12) -> None:
        try:
            if await frames.eval_frame(BFRAME_PATTERNS, _CHALLENGE_OPEN_FRAME):
                return
        except FrameUnreachable:
            pass  # bframe may not exist until the checkbox is clicked
        try:
            await frames.eval_frame(ANCHOR_PATTERNS, _CLICK_ANCHOR_FRAME)
        except FrameUnreachable:
            return
        for _ in range(tries):  # wait for the challenge frame to render
            try:
                if await frames.eval_frame(BFRAME_PATTERNS, _AUDIO_BUTTON_READY_FRAME):
                    return
            except FrameUnreachable:
                pass
            await asyncio.sleep(0.5)

    async def _wait_for_audio(self, frames: FrameAccess, *, tries: int = 16) -> Any:
        state: Any = None
        for _ in range(tries):
            state = await frames.eval_frame(BFRAME_PATTERNS, _AUDIO_STATE_FRAME)
            if isinstance(state, dict) and (state.get("download") or state.get("rate_limited")):
                return state
            await asyncio.sleep(0.4)
        return state

    async def _set_response(self, frames: FrameAccess, text: str) -> None:
        import json

        await frames.eval_frame(BFRAME_PATTERNS, _SET_RESPONSE_FRAME.replace("__ANSWER__", json.dumps(text)))

    async def _read_token_after(self, frames: FrameAccess, *, tries: int = 12) -> str:
        for _ in range(tries):
            token = await frames.token()
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
    CDP key events into the focused element (renderer-wide focus, so it reaches
    the field even inside a cross-origin frame). If voidcrawl.actions is
    unavailable (API-only env / tests), the DOM set above already populated it.
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
