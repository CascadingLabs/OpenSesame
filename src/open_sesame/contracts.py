"""Shared solver contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class CandidateAnswer:
    """One answer candidate for a direct-answer challenge."""

    text: str
    confidence: float
    source: str
    raw_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            msg = f"confidence must be between 0.0 and 1.0, got {self.confidence}"
            raise ValueError(msg)


@dataclass(frozen=True)
class SolveResult:
    """Normalized result returned by a solver."""

    kind: Literal["answer", "session_actor"]
    solver: str
    candidates: tuple[CandidateAnswer, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def best(self) -> CandidateAnswer | None:
        if not self.candidates:
            return None
        return max(self.candidates, key=lambda candidate: candidate.confidence)
