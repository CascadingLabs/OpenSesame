"""reCAPTCHA v2 engine — orders strategies, audio-first.

Vision and audio are both reCAPTCHA v2; which one runs is a *strategy* choice,
not a separate family. The audio side-door is the proven local path (fast,
reliable token mint), so it leads; the image grid is the fallback. The first
strategy that returns ``ok`` wins; otherwise the last non-ok result is returned.
"""

from __future__ import annotations

from typing import Any

from open_sesame.api.challenge import Challenge
from open_sesame.api.engines.base import Engine
from open_sesame.api.engines.recaptcha_audio import RecaptchaAudioEngine
from open_sesame.api.engines.recaptcha_grid import RecaptchaGridEngine
from open_sesame.api.policy import SolverPolicy
from open_sesame.api.registry import ModelRegistry
from open_sesame.api.result import Family, SolveResult, SolveStatus


class RecaptchaV2Engine:
    """Composes ordered reCAPTCHA v2 strategies (audio-first, grid fallback)."""

    family = Family.RECAPTCHA_V2

    def __init__(self, strategies: list[Engine]) -> None:
        self.strategies = strategies

    @classmethod
    def default(cls) -> "RecaptchaV2Engine":
        return cls([RecaptchaAudioEngine(), RecaptchaGridEngine()])

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        last: SolveResult | None = None
        # Policy may pin a single strategy order, e.g. models["recaptcha_v2_strategy"]="grid".
        for strategy in self._ordered(policy):
            result = await strategy.solve(
                challenge, page, registry=registry, policy=policy, correlation_id=correlation_id,
            )
            if result.ok:
                return result
            last = result
        return last or SolveResult(
            status=SolveStatus.FAILED, family=challenge.family, error="no reCAPTCHA strategy ran",
        )

    def _ordered(self, policy: SolverPolicy) -> list[Engine]:
        pin = policy.models.get("recaptcha_v2_strategy")
        if pin == "grid":
            return [s for s in self.strategies if isinstance(s, RecaptchaGridEngine)] or self.strategies
        if pin == "audio":
            return [s for s in self.strategies if isinstance(s, RecaptchaAudioEngine)] or self.strategies
        return self.strategies
