"""hCaptcha canvas challenge — local VLM grounding, humanized canvas clicks.

hCaptcha paints its whole challenge to a single ``<canvas>`` inside a cross-origin
iframe and asks a semantic, odd-one-out style question ("choose the card that shows a
different animal"). There are no per-tile DOM elements to read or click — the answer
cells are painted pixels with a zero-size accessibility mirror. So this engine:

  1. clicks the checkbox to open the challenge (frame-scoped DOM),
  2. screenshots the canvas region in page coordinates ("point-in-time imaging"),
  3. asks a local vision-language model to *ground* the one cell to click, as a
     normalized point (resolution-independent),
  4. maps that point onto the page and clicks it with a humanized pointer,
  5. clicks submit/next and loops the rounds, then harvests the ``h-captcha-response``
     token from the parent document.

v1 targets the **single-select** canvas task. Multi-select ("click each…") and
area-select are detected and best-effort rerolled, else returned as an honest FAILED.
Per-round latency + the model's point land in ``metadata.rounds`` for tuning.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines._hcaptcha_dom import (
    CANVAS_READY_JS,
    CHALLENGE_FRAME,
    CHECKBOX_FRAME,
    CLICK_CHECKBOX_JS,
    CLICK_RELOAD_JS,
    CLICK_SUBMIT_JS,
    STATE_JS,
    HcaptchaFrames,
    page_bbox,
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

DEFAULT_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
PROVIDER_KIND = "vlm"
STRATEGY = "canvas-vlm"


class HcaptchaEngine:
    """Solves an hCaptcha canvas challenge via VLM grounding + humanized canvas clicks."""

    family = Family.HCAPTCHA

    def __init__(
        self,
        *,
        default_model: str = DEFAULT_MODEL,
        max_rounds: int = 6,
        reload_attempts: int = 3,
        burst_frames: int = 6,
        burst_interval_s: float = 0.4,
    ) -> None:
        self.default_model = default_model
        self.max_rounds = max_rounds
        self.reload_attempts = reload_attempts
        # hCaptcha animates the cards, so a single screenshot never shows them all.
        # Capture a short burst; the VLM provider composites it into one image.
        self.burst_frames = burst_frames
        self.burst_interval_s = burst_interval_s

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        model_id = policy.models.get("hcaptcha") or self.default_model
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
        reasoner = registry.get(key)  # load-once, cached for the process
        return await self._solve(challenge, page, reasoner, key.model_id, policy.device)

    async def _solve(self, challenge, page, reasoner, model_id, device) -> SolveResult:
        frames = HcaptchaFrames(page)
        await self._open_challenge(frames)
        rounds: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            for round_index in range(self.max_rounds):
                token = await frames.token()
                if token:
                    return self._ok(challenge, token, model_id, device, rounds)

                try:
                    state = await frames.in_frame(CHALLENGE_FRAME, STATE_JS)
                except Exception:
                    break  # challenge frame gone — fall through to a final token read
                if not isinstance(state, dict) or not state.get("present"):
                    break

                prompt = str(state.get("prompt") or "")
                if not self._is_single_select(prompt):
                    if not await self._reload(frames):
                        return self._fail(
                            challenge, f"unsupported hcaptcha task: {prompt!r}", rounds
                        )
                    continue

                bbox = page_bbox(await frames.iframe_rect(), state.get("canvas"))
                if bbox is None:
                    return self._fail(challenge, "could not locate the canvas on the page", rounds)

                # Capture a temporal burst of the (animated) canvas; the provider
                # composites it so every revealed cell appears in one image.
                burst: list[str] = []
                for fi in range(self.burst_frames):
                    shot = Path(tmp) / f"hc-{round_index}-{fi}.png"
                    await page.screenshot(path=str(shot), bbox=bbox)
                    burst.append(str(shot))
                    if fi < self.burst_frames - 1:
                        await asyncio.sleep(self.burst_interval_s)

                t0 = time.monotonic()
                nx, ny, conf = await asyncio.to_thread(
                    reasoner.locate_burst, burst, instruction=prompt
                )
                infer_ms = (time.monotonic() - t0) * 1000.0
                rounds.append({
                    "round": round_index, "prompt": prompt, "frames": len(burst),
                    "point": [round(nx, 4), round(ny, 4)], "confidence": conf,
                    "infer_ms": round(infer_ms), "submit": state.get("submit"),
                })

                x = bbox[0] + nx * bbox[2]
                y = bbox[1] + ny * bbox[3]
                await self._click(page, x, y)
                await asyncio.sleep(0.4)  # let the selection register before submitting
                try:
                    await frames.in_frame(CHALLENGE_FRAME, CLICK_SUBMIT_JS)
                except Exception:
                    pass
                await asyncio.sleep(1.0)  # let the next round / token settle

            token = await self._read_token_after(frames)
            if token:
                return self._ok(challenge, token, model_id, device, rounds)
        return self._fail(challenge, "no token after hcaptcha rounds", rounds)

    async def _open_challenge(self, frames: HcaptchaFrames, *, tries: int = 20) -> None:
        """Click the checkbox to open the image challenge, if not already open."""

        try:
            if await frames.in_frame(CHALLENGE_FRAME, CANVAS_READY_JS):
                return
        except Exception:
            pass  # challenge frame may not exist until the checkbox is clicked
        try:
            await frames.in_frame(CHECKBOX_FRAME, CLICK_CHECKBOX_JS)
        except Exception:
            return  # no checkbox frame — let the round loop probe / fail honestly
        for _ in range(tries):
            try:
                if await frames.in_frame(CHALLENGE_FRAME, CANVAS_READY_JS):
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def _reload(self, frames: HcaptchaFrames) -> bool:
        """Best-effort reroll to a (hopefully) single-select challenge."""

        for _ in range(self.reload_attempts):
            try:
                clicked = await frames.in_frame(CHALLENGE_FRAME, CLICK_RELOAD_JS)
            except Exception:
                clicked = False
            if clicked:
                await asyncio.sleep(1.0)
                return True
        return False

    async def _click(self, page: Any, x: float, y: float) -> bool:
        """Humanized canvas click at page coords, with graceful fallbacks."""

        for name in ("click_xy", "click_visual_coords"):
            fn = getattr(page, name, None)
            if not callable(fn):
                continue
            try:
                await fn(x, y, humanize=True)
                return True
            except TypeError:
                await fn(x, y)
                return True
            except Exception:
                continue
        dme = getattr(page, "dispatch_mouse_event", None)
        if callable(dme):
            await dme("mouseMoved", x=x, y=y)
            await dme("mousePressed", x=x, y=y, button="left", click_count=1)
            await dme("mouseReleased", x=x, y=y, button="left", click_count=1)
            return True
        return False

    async def _read_token_after(self, frames: HcaptchaFrames, *, tries: int = 16) -> str:
        for _ in range(tries):
            token = await frames.token()
            if token:
                return token
            await asyncio.sleep(0.5)
        return ""

    # Defer what v1 can't do with one click: multi-select (counts), and temporal
    # transformation tasks. Single odd-one-out (card/icon/animal that is different
    # or breaks a pattern) IS attempted — one click on the grounded outlier.
    _DEFER = (
        "each", "all the", " all ", "every",
        " two ", " three ", " four ", " 2 ", " 3 ", " 4 ",
        "changes into", "changing", "turns into",
    )
    _ACCEPT = (
        "different", "does not", "doesn't", "not follow", "do not match",
        "odd one", "unique", "card",
    )

    @classmethod
    def _is_single_select(cls, prompt: str) -> bool:
        """True for single odd-one-out tasks v1 attempts (one click).

        hCaptcha serves several families by trust tier: the trusted tier is the
        single 'choose the card that shows a different animal'; lower tiers add
        single-select 'select the icon that does not follow the pattern' (also one
        click) and multi-select 'click the three arrows…' (deferred — needs N clicks).
        """

        p = " ".join(prompt.lower().split())
        if not p:
            return False
        if any(w in p for w in cls._DEFER):
            return False
        return any(w in p for w in cls._ACCEPT)

    def _ok(self, challenge, token, model_id, device, rounds) -> SolveResult:
        return SolveResult(
            status=SolveStatus.SOLVED, family=challenge.family,
            solution=TokenSolution(token), solved_by=SolvedBy.LOCAL,
            vendor=challenge.vendor_kind or "hcaptcha", model_id=model_id, device=device,
            metadata={"strategy": STRATEGY, "rounds": rounds},
        )

    def _fail(self, challenge, error: str, rounds) -> SolveResult:
        return SolveResult(
            status=SolveStatus.FAILED, family=challenge.family, error=error,
            metadata={"strategy": STRATEGY, "rounds": rounds},
        )
