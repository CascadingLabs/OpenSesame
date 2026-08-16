from __future__ import annotations

import importlib.metadata
import os
from dataclasses import dataclass

from fastapi import FastAPI

from opensesame.multimodal.planner import (
    MultimodalPlanner,
    build_planner,
    unavailable_decision,
)
from opensesame.multimodal.schemas import (
    ActionKind,
    Capability,
    ChallengeDecision,
    ChallengeKind,
    ChallengeObservation,
    HealthResponse,
    HostCapabilities,
    Modality,
    RecaptchaGridSelectionRequest,
    RecaptchaGridSelectionResponse,
)


@dataclass(frozen=True)
class MultimodalHostSettings:
    """Runtime settings for the local-only multimodal solver host."""

    model: str | None = None
    completions_url: str | None = None
    completions_timeout_s: float = 45.0


def package_version() -> str:
    try:
        return importlib.metadata.version("opensesame")
    except importlib.metadata.PackageNotFoundError:
        return "0+local"


def _safe_decision(
    observation: ChallengeObservation, decision: ChallengeDecision
) -> ChallengeDecision:
    """Fail closed if a planner returns an action the caller cannot execute."""

    if decision.action not in observation.allowed_actions:
        return unavailable_decision(
            observation,
            reason=f"planner returned disallowed action: {decision.action.value}",
            metadata={
                "reason": "planner_disallowed_action",
                "returned_action": decision.action.value,
            },
        )
    if (
        decision.action == ActionKind.SELECT_TILES
        and observation.rows
        and observation.cols
    ):
        for tile in decision.tiles:
            if tile.row >= observation.rows or tile.col >= observation.cols:
                return unavailable_decision(
                    observation,
                    reason=(
                        "planner returned out-of-bounds tile: "
                        f"row={tile.row}, col={tile.col}"
                    ),
                    metadata={
                        "reason": "planner_out_of_bounds_tile",
                        "rows": observation.rows,
                        "cols": observation.cols,
                        "row": tile.row,
                        "col": tile.col,
                    },
                )
    return decision


def create_app(
    settings: MultimodalHostSettings | None = None,
    *,
    planner: MultimodalPlanner | None = None,
) -> FastAPI:
    resolved = settings or MultimodalHostSettings()
    resolved_planner = planner or build_planner(
        resolved.model,
        completions_url=resolved.completions_url,
        completions_timeout_s=resolved.completions_timeout_s,
    )

    app = FastAPI(title="OpenSesame multimodal host")
    app.state.planner = resolved_planner
    app.state.settings = resolved

    @app.get("/health")
    async def health() -> dict[str, object]:
        response = HealthResponse(
            ok=True,
            ready=resolved_planner.ready,
            model_id=resolved_planner.model_id,
            reason=None if resolved_planner.ready else "planner unavailable",
        )
        return response.model_dump(mode="json")

    @app.get("/capabilities")
    async def capabilities() -> dict[str, object]:
        response = HostCapabilities(
            version=package_version(),
            ready=resolved_planner.ready,
            model_id=resolved_planner.model_id,
            capabilities=(
                Capability(
                    name="recaptcha.grid.select",
                    challenge_kinds=(ChallengeKind.RECAPTCHA_GRID,),
                    modalities=(
                        Modality.IMAGE,
                        Modality.TEXT,
                        Modality.ACTION_HISTORY,
                    ),
                    actions=(
                        ActionKind.SELECT_TILES,
                        ActionKind.VERIFY,
                        ActionKind.REFRESH,
                        ActionKind.ESCALATE,
                    ),
                    endpoint="/v1/recaptcha/grid/select",
                    status="ready" if resolved_planner.ready else "unavailable",
                ),
                Capability(
                    name="challenge.next_action",
                    challenge_kinds=(
                        ChallengeKind.RECAPTCHA_GRID,
                        ChallengeKind.CLOUDFLARE_TURNSTILE,
                    ),
                    modalities=(
                        Modality.IMAGE,
                        Modality.TEXT,
                        Modality.ACTION_HISTORY,
                    ),
                    actions=(
                        ActionKind.SELECT_TILES,
                        ActionKind.VERIFY,
                        ActionKind.WAIT,
                        ActionKind.REFRESH,
                        ActionKind.ESCALATE,
                    ),
                    endpoint="/v1/challenges/next-action",
                    status="scaffold" if resolved_planner.ready else "unavailable",
                ),
            ),
        )
        return response.model_dump(mode="json")

    @app.post("/v1/challenges/next-action")
    async def next_action(observation: ChallengeObservation) -> dict[str, object]:
        decision = _safe_decision(
            observation, await resolved_planner.decide(observation)
        )
        return decision.model_dump(mode="json")

    @app.post("/v1/recaptcha/grid/select")
    async def recaptcha_grid_select(
        request: RecaptchaGridSelectionRequest,
    ) -> dict[str, object]:
        observation = request.to_observation()
        decision = _safe_decision(
            observation, await resolved_planner.decide(observation)
        )
        response = RecaptchaGridSelectionResponse.from_decision(decision)
        return response.model_dump(mode="json")

    return app


def create_app_from_env() -> FastAPI:
    """Granian factory for `opensesame multimodal serve`."""

    timeout = os.environ.get("OPENSESAME_MM_COMPLETIONS_TIMEOUT_S")
    return create_app(
        MultimodalHostSettings(
            model=os.environ.get("OPENSESAME_MM_MODEL") or None,
            completions_url=os.environ.get("OPENSESAME_MM_COMPLETIONS_URL") or None,
            completions_timeout_s=float(timeout) if timeout else 45.0,
        )
    )
