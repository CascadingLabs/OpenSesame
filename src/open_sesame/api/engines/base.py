"""Engine + manual-solver protocols.

An engine turns a (challenge, live page) into a ``SolveResult`` using local
models and DOM-level manipulation. It NEVER owns the browser — it drives the
page it is handed. The manual solver is the human-in-the-loop escalation target;
v1 ships an in-process callback, V2 swaps in a noVNC queue behind the same
protocol with zero engine changes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from open_sesame.api.challenge import Challenge
from open_sesame.api.policy import SolverPolicy
from open_sesame.api.registry import ModelRegistry
from open_sesame.api.result import SolveResult


@runtime_checkable
class Engine(Protocol):
    """A local-model solver for one challenge family."""

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult: ...


@runtime_checkable
class ManualSolver(Protocol):
    """Human-in-the-loop escalation target (in-process callback in v1)."""

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        timeout_s: float,
        correlation_id: str | None = None,
    ) -> SolveResult: ...
