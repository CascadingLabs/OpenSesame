"""OpenSesame — self-hosted captcha/token solving, no paid solver APIs.

Public API:

    from OpenSesame import Solver, SolverPolicy, Challenge

    solver = Solver(policy=SolverPolicy.auto_only(allow_sites=["www.google.com"]))
    async with solver.engine():
        result = await solver.solve(challenge, page=page)
    if result.ok and result.solution.is_token:
        await page.inject_captcha_token(result.token)
"""

from __future__ import annotations

from OpenSesame.api import (
    AnswerSolution,
    Challenge,
    Delivery,
    Family,
    ModelKey,
    ModelRegistry,
    SiteNotAllowed,
    Solution,
    SolvedBy,
    SolveResult,
    SolveStatus,
    Solver,
    SolverPolicy,
    Ticket,
    TokenSolution,
    default_registry,
    load_policy,
)

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
    "TokenSolution",
    "default_registry",
    "load_policy",
]
