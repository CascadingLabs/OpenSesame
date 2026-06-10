"""OpenSesame public solver API."""

from __future__ import annotations

from OpenSesame.api.challenge import Challenge, WidgetRect
from OpenSesame.api.policy import SiteNotAllowed, SolverPolicy, load_policy
from OpenSesame.api.registry import ModelKey, ModelRegistry, default_registry
from OpenSesame.api.result import (
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
from OpenSesame.api.solver import Solver, Ticket

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
