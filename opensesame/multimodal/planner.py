from __future__ import annotations

import base64
import json
from importlib import import_module
from typing import Any, Protocol
from urllib.error import URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from opensesame.multimodal.schemas import (
    ActionKind,
    ChallengeDecision,
    ChallengeObservation,
)


class MultimodalPlanner(Protocol):
    """One-step planner for challenge observations.

    Planners only decide. The caller owns browser state, clicks, verification,
    retries, and human escalation.
    """

    @property
    def model_id(self) -> str | None: ...

    @property
    def ready(self) -> bool: ...

    async def decide(self, observation: ChallengeObservation) -> ChallengeDecision: ...


class UnavailablePlanner:
    """Safe default when no local Pydantic AI runtime/model is configured."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    @property
    def model_id(self) -> str | None:
        return None

    @property
    def ready(self) -> bool:
        return False

    async def decide(self, observation: ChallengeObservation) -> ChallengeDecision:
        return unavailable_decision(
            observation,
            reason=self.reason,
            metadata={"reason": "planner_unavailable"},
        )


class LocalCompletionsPlanner:
    """OpenAI-compatible localhost completions/chat-completions adapter.

    Configure with either a full URL or shorthand ``localhost://PORT``. The
    shorthand resolves to ``http://127.0.0.1:PORT/v1/chat/completions`` and sends
    images as data URLs in the standard chat-completions image content shape.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str | None = None,
        timeout_s: float = 45.0,
    ) -> None:
        self._url = normalize_completions_url(base_url)
        self._model = model or "local-vlm"
        self._timeout_s = timeout_s

    @property
    def model_id(self) -> str | None:
        return self._model

    @property
    def ready(self) -> bool:
        return True

    async def decide(self, observation: ChallengeObservation) -> ChallengeDecision:
        payload = completions_payload(observation, model=self._model)
        try:
            body = await _post_json(self._url, payload, timeout_s=self._timeout_s)
            return parse_completion_decision(body, model_id=self._model)
        except Exception as exc:
            return unavailable_decision(
                observation,
                reason=f"local completions planner failed: {type(exc).__name__}: {exc}",
                metadata={"reason": "local_completions_failed", "url": self._url},
            )


class PydanticAiPlanner:
    """Pydantic AI V2 adapter behind the local multimodal host.

    The import is lazy so the human-takeover UI keeps working without the local
    multimodal extra installed. The adapter intentionally exposes one method,
    ``decide``; endpoint-specific code wraps it for reCAPTCHA or future vendors.
    """

    def __init__(
        self,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        try:
            pydantic_ai: Any = import_module("pydantic_ai")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pydantic-ai v2 is required for the multimodal planner; "
                "install it in the local solver host environment."
            ) from exc
        agent_cls = pydantic_ai.Agent
        try:
            self._agent = agent_cls(
                model,
                output_type=ChallengeDecision,
                system_prompt=self._system_prompt,
            )
        except TypeError as exc:
            raise RuntimeError(
                "Pydantic AI Agent did not accept the v2 output_type API. "
                "Install/activate Pydantic AI v2 for opensesame mm-host."
            ) from exc

    @property
    def model_id(self) -> str | None:
        return self._model

    @property
    def ready(self) -> bool:
        return True

    async def decide(self, observation: ChallengeObservation) -> ChallengeDecision:
        prompt = pydantic_ai_input(observation)
        result = await self._agent.run(prompt)
        output = getattr(result, "output", None)
        if isinstance(output, ChallengeDecision):
            return output
        data = getattr(result, "data", None)
        if isinstance(data, ChallengeDecision):  # defensive for older local installs
            return data
        return ChallengeDecision.model_validate(output if output is not None else data)


DEFAULT_SYSTEM_PROMPT = """\
You are OpenSesame's local multimodal challenge planner.
Return only actions allowed by the observation. For reCAPTCHA image grids, select
only tiles that match the instruction. Do not claim that browser actions were
performed: the caller executes clicks and will send another observation if needed.
Escalate rather than guessing when the image or instruction is ambiguous.
"""


def observation_prompt(observation: ChallengeObservation) -> str:
    """Compact, deterministic text prompt paired with the image payload."""

    payload = observation.model_dump(mode="json", exclude={"image"})
    return (
        "Decide the next challenge action from this observation. "
        "The image is attached as separate multimodal content when supported. "
        "Do not execute actions.\n"
        f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
    )


def pydantic_ai_input(observation: ChallengeObservation) -> Any:
    """Return Pydantic AI multimodal input, with a text-only fallback.

    Pydantic AI V2 exposes binary/image content helpers. Keeping this conversion
    isolated prevents endpoint code and tests from depending on that API surface.
    """

    prompt = observation_prompt(observation)
    try:
        pydantic_ai: Any = import_module("pydantic_ai")
        binary_content = pydantic_ai.BinaryContent
        image_bytes = base64.b64decode(observation.image.data_base64)
        return [
            prompt,
            binary_content(data=image_bytes, media_type=observation.image.mime_type),
        ]
    except Exception:
        # Still useful for local fake/text-only models and keeps the host safe if
        # the installed Pydantic AI image helper is renamed.
        return (
            f"{prompt}\n"
            f"image_mime_type={observation.image.mime_type}\n"
            f"image_base64={observation.image.data_base64}"
        )


def normalize_completions_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme == "localhost":
        port = parsed.netloc or parsed.path.lstrip("/")
        if not port:
            raise ValueError("localhost completions URL must include a port")
        return f"http://127.0.0.1:{port}/v1/chat/completions"
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("completions URL must be http(s) or localhost://PORT")
    if parsed.path in {"", "/"}:
        return urlunparse(parsed._replace(path="/v1/chat/completions"))
    return value


def completions_payload(
    observation: ChallengeObservation, *, model: str
) -> dict[str, Any]:
    schema = ChallengeDecision.model_json_schema()
    text = (
        f"{DEFAULT_SYSTEM_PROMPT}\n"
        f"Return strict JSON matching this schema. No markdown.\n"
        f"schema={json.dumps(schema, sort_keys=True, separators=(',', ':'))}\n"
        f"{observation_prompt(observation)}"
    )
    image_url = (
        f"data:{observation.image.mime_type};base64,{observation.image.data_base64}"
    )
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


async def _post_json(url: str, payload: dict[str, Any], *, timeout_s: float) -> Any:
    import asyncio

    return await asyncio.to_thread(_post_json_sync, url, payload, timeout_s)


def _post_json_sync(url: str, payload: dict[str, Any], timeout_s: float) -> Any:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8")
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc
    return json.loads(raw)


def parse_completion_decision(body: Any, *, model_id: str) -> ChallengeDecision:
    content = completion_text(body)
    raw = extract_json_object(content)
    decision = ChallengeDecision.model_validate_json(raw)
    if decision.model_id is None:
        decision.model_id = model_id
    return decision


def completion_text(body: Any) -> str:
    if not isinstance(body, dict):
        raise ValueError("completion response must be an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("completion response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("completion choice must be an object")
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    text = first.get("text")
    if isinstance(text, str):
        return text
    raise ValueError("completion choice has no text content")


def extract_json_object(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text.startswith("{"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("completion text did not contain a JSON object")
    return text[start : end + 1]


def unavailable_decision(
    observation: ChallengeObservation,
    *,
    reason: str,
    metadata: dict[str, Any],
) -> ChallengeDecision:
    action = (
        ActionKind.ESCALATE
        if ActionKind.ESCALATE in observation.allowed_actions
        else observation.allowed_actions[0]
    )
    return ChallengeDecision(
        action=action,
        confidence=0.0,
        reason=reason,
        needs_observation_after_action=False,
        metadata=metadata,
    )


def build_planner(
    model: str | None,
    *,
    completions_url: str | None = None,
    completions_timeout_s: float = 45.0,
) -> MultimodalPlanner:
    if completions_url:
        try:
            return LocalCompletionsPlanner(
                base_url=completions_url,
                model=model,
                timeout_s=completions_timeout_s,
            )
        except ValueError as exc:
            return UnavailablePlanner(str(exc))
    if not model:
        return UnavailablePlanner("no multimodal model configured")
    try:
        return PydanticAiPlanner(model=model)
    except RuntimeError as exc:
        return UnavailablePlanner(str(exc))
