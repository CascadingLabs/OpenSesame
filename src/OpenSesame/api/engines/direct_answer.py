"""Direct-answer OCR captchas — local text recognition.

Distorted-text captchas have no token: the recognized string *is* the answer,
compared server-side. The engine captures the captcha image (an element selector,
or a pre-captured path in the challenge metadata), runs a registry ``TextReader``,
and returns an ``AnswerSolution`` (delivery=ANSWER). Typing the answer into the
form is the caller's step; if a ``response_field_selector`` is present the engine
will fill it via VoidCrawl's DOM input action.
"""

from __future__ import annotations

import asyncio
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    AnswerSolution,
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
)

DEFAULT_MODEL = "grafj-conv-transformer-base"
PROVIDER_KIND = "ocr"

_ELEMENT_BBOX = r"""
(() => {
  const el = document.querySelector(__SEL__);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {x:r.left, y:r.top, width:r.width, height:r.height};
})()
"""


class DirectAnswerEngine:
    """Solves OCR / distorted-text captchas with a local text recognizer."""

    family = Family.OCR

    def __init__(self, *, default_model: str = DEFAULT_MODEL) -> None:
        self.default_model = default_model

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        model_id = policy.models.get("ocr") or self.default_model
        key = ModelKey(kind=PROVIDER_KIND, model_id=model_id, device=policy.device)
        reader = registry.acquire(key)
        try:
            return await self._solve(challenge, page, reader, model_id, policy.device)
        finally:
            registry.release(key)

    async def _solve(self, challenge, page, reader, model_id, device) -> SolveResult:
        image_path = challenge.metadata.get("image_path")
        if not image_path:
            image_path = await self._capture_image(challenge, page)
        if not image_path:
            return SolveResult(status=SolveStatus.FAILED, family=challenge.family,
                               error="no captcha image (set metadata image_path or image_selector)")

        text, confidence = await asyncio.to_thread(reader.read_text, str(image_path))
        if not text:
            return SolveResult(status=SolveStatus.FAILED, family=challenge.family,
                               error="OCR produced no text", model_id=model_id, device=device)

        if challenge.response_field_selector:
            await fill_via_actions(page, challenge.response_field_selector, text)

        return SolveResult(
            status=SolveStatus.SOLVED, family=challenge.family,
            solution=AnswerSolution(text), solved_by=SolvedBy.LOCAL,
            model_id=model_id, device=device, confidence=confidence,
            metadata={"strategy": "ocr"},
        )

    async def _capture_image(self, challenge, page) -> str | None:
        import json

        selector = challenge.metadata.get("image_selector")
        if not selector:
            return None
        bbox = await page.eval_js(_ELEMENT_BBOX.replace("__SEL__", json.dumps(selector)))
        if not isinstance(bbox, dict):
            return None
        out = challenge.metadata.get("capture_to", "/tmp/opensesame-ocr.png")
        await page.screenshot(
            path=str(out),
            bbox=(int(bbox["x"]), int(bbox["y"]), int(bbox["width"]), int(bbox["height"])),
        )
        return str(out)


async def fill_via_actions(page: Any, selector: str, text: str) -> None:
    """Set a form field via VoidCrawl's DOM input action (best-effort)."""

    try:
        from voidcrawl.actions import Flow
        from voidcrawl.actions.builtin.input import SetInputValue
    except Exception:
        return
    try:
        await Flow([SetInputValue(selector, text)]).run(page)
    except Exception:
        pass
