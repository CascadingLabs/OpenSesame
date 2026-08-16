from __future__ import annotations

from typing import Any, cast

import pytest

from opensesame.multimodal import planner
from opensesame.multimodal.planner import (
    LocalCompletionsPlanner,
    normalize_completions_url,
)
from opensesame.multimodal.schemas import (
    ChallengeKind,
    ChallengeObservation,
    ImageInput,
)


def test_localhost_completions_url_shorthand_defaults_to_chat_completions() -> None:
    assert (
        normalize_completions_url("localhost://12345")
        == "http://127.0.0.1:12345/v1/chat/completions"
    )
    assert (
        normalize_completions_url("http://127.0.0.1:8080")
        == "http://127.0.0.1:8080/v1/chat/completions"
    )


@pytest.mark.asyncio
async def test_local_completions_planner_posts_image_and_validates_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object], float]] = []

    async def fake_post_json(
        url: str, payload: dict[str, object], *, timeout_s: float
    ) -> dict[str, object]:
        calls.append((url, payload, timeout_s))
        content = (
            '{"action":"select_tiles",'
            '"tiles":[{"row":0,"col":1,"confidence":0.88}],'
            '"confidence":0.88}'
        )
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(planner, "_post_json", fake_post_json)
    local = LocalCompletionsPlanner(
        base_url="localhost://9999", model="local-vlm", timeout_s=3.0
    )

    decision = await local.decide(
        ChallengeObservation(
            kind=ChallengeKind.RECAPTCHA_GRID,
            image=ImageInput(data_base64="aW1hZ2U="),
            instruction="Select all buses",
            rows=3,
            cols=3,
        )
    )

    assert decision.action == "select_tiles"
    assert decision.tiles[0].row == 0
    assert decision.tiles[0].col == 1
    assert decision.model_id == "local-vlm"
    assert calls[0][0] == "http://127.0.0.1:9999/v1/chat/completions"
    assert calls[0][2] == 3.0
    messages = cast(list[dict[str, Any]], calls[0][1]["messages"])
    content = cast(list[dict[str, Any]], messages[0]["content"])
    assert content[1]["image_url"]["url"] == "data:image/png;base64,aW1hZ2U="


@pytest.mark.asyncio
async def test_local_completions_planner_escalates_on_bad_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_json(
        url: str, payload: dict[str, object], *, timeout_s: float
    ) -> dict[str, object]:
        return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr(planner, "_post_json", fake_post_json)
    local = LocalCompletionsPlanner(base_url="localhost://9999")

    decision = await local.decide(
        ChallengeObservation(
            kind=ChallengeKind.RECAPTCHA_GRID,
            image=ImageInput(data_base64="aW1hZ2U="),
            instruction="Select all buses",
            rows=3,
            cols=3,
        )
    )

    assert decision.action == "escalate"
    assert decision.metadata["reason"] == "local_completions_failed"
