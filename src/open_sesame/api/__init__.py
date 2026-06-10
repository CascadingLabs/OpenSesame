"""OpenSesame public solver API."""

from __future__ import annotations

from open_sesame.api.challenge import Challenge, WidgetRect
from open_sesame.api.policy import SiteNotAllowed, SolverPolicy, load_policy
from open_sesame.api.registry import ModelKey, ModelRegistry, default_registry
from open_sesame.api.result import (
    AnswerSolution,
    Delivery,
    Family,
    Solution,
    SolvedBy,
    SolveResult,
    SolveStatus,
    Timing,
    TokenSolution,
)
from open_sesame.api.solver import Solver, Ticket

__all__ = [
    "AnswerSolution",
    "Challenge",
    "Delivery",
    "Family",
    "ModelKey",
    "ModelRegistry",
    "SiteNotAllowed",
    "Solution",
    "SolveResult",
    "SolveStatus",
    "SolvedBy",
    "Solver",
    "SolverPolicy",
    "Ticket",
    "Timing",
    "TokenSolution",
    "WidgetRect",
    "default_registry",
    "load_policy",
]
