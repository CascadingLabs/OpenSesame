from __future__ import annotations

from fastapi.testclient import TestClient

from opensesame.multimodal.schemas import (
    ActionKind,
    ChallengeDecision,
    ChallengeObservation,
    TileSelection,
)
from opensesame.multimodal.server import create_app


class FakePlanner:
    @property
    def model_id(self) -> str:
        return "fake-vlm"

    @property
    def ready(self) -> bool:
        return True

    async def decide(self, observation: ChallengeObservation) -> ChallengeDecision:
        assert observation.kind == "recaptcha.grid"
        assert observation.rows == 3
        assert observation.cols == 3
        return ChallengeDecision(
            action=ActionKind.SELECT_TILES,
            tiles=(TileSelection(row=1, col=2, confidence=0.91),),
            confidence=0.91,
            model_id=self.model_id,
            reason="matched target",
        )


class UnsafePlanner(FakePlanner):
    def __init__(self, decision: ChallengeDecision) -> None:
        self.decision = decision

    async def decide(self, observation: ChallengeObservation) -> ChallengeDecision:
        return self.decision


def test_multimodal_host_capabilities_contract() -> None:
    app = create_app(planner=FakePlanner())

    with TestClient(app) as client:
        health = client.get("/health")
        capabilities = client.get("/capabilities")

    assert health.status_code == 200
    assert health.json() == {
        "ok": True,
        "service": "opensesame-multimodal-host",
        "ready": True,
        "model_id": "fake-vlm",
        "reason": None,
    }
    assert capabilities.status_code == 200
    body = capabilities.json()
    assert body["ready"] is True
    assert body["model_id"] == "fake-vlm"
    assert [capability["endpoint"] for capability in body["capabilities"]] == [
        "/v1/recaptcha/grid/select",
        "/v1/challenges/next-action",
    ]


def test_recaptcha_grid_select_contract_keeps_clicks_out_of_host() -> None:
    app = create_app(planner=FakePlanner())

    with TestClient(app) as client:
        response = client.post(
            "/v1/recaptcha/grid/select",
            json={
                "image": {"data_base64": "aW1hZ2U=", "mime_type": "image/png"},
                "rows": 3,
                "cols": 3,
                "instruction": "Select all images with buses",
                "target": "buses",
                "round_index": 0,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "action": "select_tiles",
        "tiles": [{"row": 1, "col": 2, "confidence": 0.91, "rationale": None}],
        "confidence": 0.91,
        "reason": "matched target",
        "model_id": "fake-vlm",
        "metadata": {},
    }


def test_multimodal_host_rejects_invalid_image_payload() -> None:
    app = create_app(planner=FakePlanner())

    with TestClient(app) as client:
        response = client.post(
            "/v1/recaptcha/grid/select",
            json={
                "image": {"data_base64": "not base64!!!"},
                "rows": 3,
                "cols": 3,
                "instruction": "Select all images with buses",
            },
        )

    assert response.status_code == 422
    assert "data_base64 must be valid base64" in response.text


def test_multimodal_host_fails_closed_on_disallowed_planner_action() -> None:
    app = create_app(
        planner=UnsafePlanner(
            ChallengeDecision(action=ActionKind.WAIT, confidence=0.8)
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/recaptcha/grid/select",
            json={
                "image": {"data_base64": "aW1hZ2U="},
                "rows": 3,
                "cols": 3,
                "instruction": "Select all images with buses",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["action"] == "escalate"
    assert body["metadata"]["reason"] == "planner_disallowed_action"


def test_multimodal_host_fails_closed_on_out_of_bounds_tiles() -> None:
    app = create_app(
        planner=UnsafePlanner(
            ChallengeDecision(
                action=ActionKind.SELECT_TILES,
                tiles=(TileSelection(row=3, col=0, confidence=0.7),),
                confidence=0.7,
            )
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/recaptcha/grid/select",
            json={
                "image": {"data_base64": "aW1hZ2U="},
                "rows": 3,
                "cols": 3,
                "instruction": "Select all images with buses",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["action"] == "escalate"
    assert body["metadata"]["reason"] == "planner_out_of_bounds_tile"


def test_unconfigured_multimodal_host_escalates_safely() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/v1/recaptcha/grid/select",
            json={
                "image": {"data_base64": "aW1hZ2U="},
                "rows": 3,
                "cols": 3,
                "instruction": "Select all images with buses",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["action"] == "escalate"
    assert body["metadata"] == {"reason": "planner_unavailable"}
