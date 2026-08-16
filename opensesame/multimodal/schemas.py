from __future__ import annotations

import base64
import binascii
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChallengeKind(str, Enum):
    RECAPTCHA_GRID = "recaptcha.grid"
    CLOUDFLARE_TURNSTILE = "cloudflare.turnstile"


class Modality(str, Enum):
    IMAGE = "image"
    TEXT = "text"
    ACTION_HISTORY = "action_history"


class ActionKind(str, Enum):
    SELECT_TILES = "select_tiles"
    VERIFY = "verify"
    WAIT = "wait"
    REFRESH = "refresh"
    ESCALATE = "escalate"


class ImageInput(BaseModel):
    """Inline image payload sent to the local multimodal host."""

    model_config = ConfigDict(extra="forbid")

    data_base64: str = Field(min_length=1)
    mime_type: str = "image/png"

    @field_validator("data_base64")
    @classmethod
    def valid_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("data_base64 must be valid base64") from exc
        return value

    @field_validator("mime_type")
    @classmethod
    def image_mime_type(cls, value: str) -> str:
        if not value.startswith("image/"):
            raise ValueError("mime_type must be image/*")
        return value


class TileCoordinate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row: int = Field(ge=0)
    col: int = Field(ge=0)


class TileSelection(TileCoordinate):
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None


class ActionRecord(BaseModel):
    """A prior action/result pair so an LLM can reason iteratively."""

    model_config = ConfigDict(extra="forbid")

    action: ActionKind
    tiles: tuple[TileCoordinate, ...] = ()
    result: str | None = None
    note: str | None = None


class ChallengeObservation(BaseModel):
    """Vendor-neutral observation for a one-step multimodal decision."""

    model_config = ConfigDict(extra="forbid")

    kind: ChallengeKind
    image: ImageInput
    instruction: str = Field(min_length=1)
    rows: int | None = Field(default=None, ge=1, le=10)
    cols: int | None = Field(default=None, ge=1, le=10)
    target: str | None = None
    round_index: int = Field(default=0, ge=0)
    selected_tiles: tuple[TileCoordinate, ...] = ()
    action_history: tuple[ActionRecord, ...] = ()
    allowed_actions: tuple[ActionKind, ...] = (
        ActionKind.SELECT_TILES,
        ActionKind.VERIFY,
        ActionKind.REFRESH,
        ActionKind.ESCALATE,
    )
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_actions")
    @classmethod
    def non_empty_actions(cls, value: tuple[ActionKind, ...]) -> tuple[ActionKind, ...]:
        if not value:
            raise ValueError("allowed_actions must not be empty")
        return value


class ChallengeDecision(BaseModel):
    """A model decision. The browser/engine remains responsible for execution."""

    model_config = ConfigDict(extra="forbid")

    action: ActionKind
    tiles: tuple[TileSelection, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str | None = None
    model_id: str | None = None
    needs_observation_after_action: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecaptchaGridSelectionRequest(BaseModel):
    """Narrow happy-path request used by the reCAPTCHA grid engine."""

    model_config = ConfigDict(extra="forbid")

    image: ImageInput
    rows: int = Field(ge=1, le=10)
    cols: int = Field(ge=1, le=10)
    instruction: str = Field(min_length=1)
    target: str | None = None
    round_index: int = Field(default=0, ge=0)
    selected_tiles: tuple[TileCoordinate, ...] = ()
    action_history: tuple[ActionRecord, ...] = ()
    context: dict[str, Any] = Field(default_factory=dict)

    def to_observation(self) -> ChallengeObservation:
        return ChallengeObservation(
            kind=ChallengeKind.RECAPTCHA_GRID,
            image=self.image,
            instruction=self.instruction,
            rows=self.rows,
            cols=self.cols,
            target=self.target,
            round_index=self.round_index,
            selected_tiles=self.selected_tiles,
            action_history=self.action_history,
            allowed_actions=(
                ActionKind.SELECT_TILES,
                ActionKind.VERIFY,
                ActionKind.REFRESH,
                ActionKind.ESCALATE,
            ),
            context=self.context,
        )


class RecaptchaGridSelectionResponse(BaseModel):
    """Stable tile-selection response; engines translate it into DOM clicks."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    action: ActionKind
    tiles: tuple[TileSelection, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str | None = None
    model_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_decision(
        cls, decision: ChallengeDecision
    ) -> RecaptchaGridSelectionResponse:
        return cls(
            ok=decision.action in {ActionKind.SELECT_TILES, ActionKind.VERIFY},
            action=decision.action,
            tiles=decision.tiles,
            confidence=decision.confidence,
            reason=decision.reason,
            model_id=decision.model_id,
            metadata=decision.metadata,
        )


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    challenge_kinds: tuple[ChallengeKind, ...]
    modalities: tuple[Modality, ...]
    actions: tuple[ActionKind, ...]
    endpoint: str
    status: Literal["ready", "scaffold", "unavailable"] = "scaffold"


class HostCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str = "opensesame-multimodal-host"
    version: str
    ready: bool
    model_id: str | None = None
    capabilities: tuple[Capability, ...]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    service: str = "opensesame-multimodal-host"
    ready: bool
    model_id: str | None = None
    reason: str | None = None
