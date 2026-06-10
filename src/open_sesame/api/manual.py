"""In-process manual (human-in-the-loop) escalation adapter.

V1 ships a callback: register an async function that drives a human solve (e.g.
hand the live page to an operator, or a noVNC session) and returns a
``SolveResult``. The Solver routes to it on ``escalate_on_fail`` when the local
engine fails. V2 swaps a noVNC queue + notifications behind this same
``ManualSolver`` protocol with no Solver changes.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from open_sesame.api.challenge import Challenge
from open_sesame.api.result import SolveResult

ManualCallback = Callable[[Challenge, Any, float, "str | None"], Awaitable[SolveResult]]


class CallbackManualSolver:
    """Wraps an async callback as a :class:`ManualSolver`."""

    def __init__(self, callback: ManualCallback) -> None:
        self._callback = callback

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        timeout_s: float,
        correlation_id: str | None = None,
    ) -> SolveResult:
        return await self._callback(challenge, page, timeout_s, correlation_id)
