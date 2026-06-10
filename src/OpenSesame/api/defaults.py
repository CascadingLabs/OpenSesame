"""Wiring helpers: register the default engines and (best-effort) ML providers.

``default_solver(policy)`` is the ergonomic entry point — a Solver with the three
v1 engines registered (reCAPTCHA v2 audio+grid, OCR). ``install_default_providers``
registers the model factories from the solver ML modules *if they are installed*;
on an API-only checkout they are absent, so engines raise a clear ``LookupError``
on ``acquire`` until the ML extras + solver modules are present.
"""

from __future__ import annotations

from typing import Any

from OpenSesame.api.engines.direct_answer import DirectAnswerEngine
from OpenSesame.api.engines.recaptcha import RecaptchaV2Engine
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelRegistry, default_registry
from OpenSesame.api.result import Family
from OpenSesame.api.solver import Solver


def register_default_engines(solver: Solver) -> None:
    recaptcha = RecaptchaV2Engine.default()
    solver.register_engine(Family.RECAPTCHA_V2, recaptcha)
    solver.register_engine(Family.RECAPTCHA_V2_INVISIBLE, recaptcha)
    solver.register_engine(Family.OCR, DirectAnswerEngine())


def install_default_providers(registry: ModelRegistry | None = None) -> ModelRegistry:
    """Register model factories from the solver ML modules, if installed."""

    reg = registry or default_registry()
    _try_register_whisper(reg)
    _try_register_tiles(reg)
    _try_register_ocr(reg)
    return reg


def default_solver(policy: SolverPolicy, **kwargs: Any) -> Solver:
    solver = Solver(policy, **kwargs)
    register_default_engines(solver)
    install_default_providers(solver.registry)
    return solver


def _try_register_whisper(reg: ModelRegistry) -> None:
    if reg.has_factory("whisper"):
        return
    try:
        from OpenSesame.solvers.whisper_provider import build_transcriber  # type: ignore
    except Exception:
        return
    reg.register_factory("whisper", lambda key: build_transcriber(key.model_id, key.device))


def _try_register_tiles(reg: ModelRegistry) -> None:
    if reg.has_factory("tiles"):
        return
    try:
        from OpenSesame.solvers.tile_provider import build_tile_selector  # type: ignore
    except Exception:
        return
    reg.register_factory("tiles", lambda key: build_tile_selector(key.model_id, key.device))


def _try_register_ocr(reg: ModelRegistry) -> None:
    if reg.has_factory("ocr"):
        return
    try:
        from OpenSesame.solvers.ocr_provider import build_text_reader  # type: ignore
    except Exception:
        return
    reg.register_factory("ocr", lambda key: build_text_reader(key.model_id, key.device))
