"""Wiring helpers: the opinionated, batteries-included Solver.

``default_solver(policy)`` returns a Solver with the three v1 engines registered
(reCAPTCHA v2 audio+grid, OCR) and the built-in local-model providers installed
(Whisper, ViT tiles, Tesseract OCR) when their dependencies are present. The
caller names a model in policy; OpenSesame owns the tile splitting, label
normalization, and ASR/OCR wrapping. On an API-only checkout the ML providers are
absent, so an engine raises a clear ``LookupError`` until the extra is installed.
"""

from __future__ import annotations

from typing import Any

from OpenSesame.api.builtin import register_builtin_providers
from OpenSesame.api.engines.direct_answer import DirectAnswerEngine
from OpenSesame.api.engines.recaptcha import RecaptchaV2Engine
from OpenSesame.api.engines.turnstile import TurnstileEngine
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelRegistry, default_registry
from OpenSesame.api.result import Family
from OpenSesame.api.solver import Solver


def register_default_engines(solver: Solver) -> None:
    recaptcha = RecaptchaV2Engine.default()
    solver.register_engine(Family.RECAPTCHA_V2, recaptcha)
    solver.register_engine(Family.RECAPTCHA_V2_INVISIBLE, recaptcha)
    solver.register_engine(Family.OCR, DirectAnswerEngine())
    solver.register_engine(Family.TURNSTILE, TurnstileEngine())


def install_default_providers(registry: ModelRegistry | None = None) -> ModelRegistry:
    """Register the built-in local-model providers whose deps are installed."""

    reg = registry or default_registry()
    register_builtin_providers(reg)
    return reg


def default_solver(policy: SolverPolicy, **kwargs: Any) -> Solver:
    solver = Solver(policy, **kwargs)
    register_default_engines(solver)
    install_default_providers(solver.registry)
    return solver
