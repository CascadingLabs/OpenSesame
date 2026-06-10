"""reCAPTCHA v2 engine — orders strategies, audio-first.

Vision and audio are both reCAPTCHA v2; which one runs is a *strategy* choice,
not a separate family. The audio side-door is the proven local path (fast,
reliable token mint), so it leads; the image grid is the fallback. The first
strategy that returns ``ok`` wins; otherwise the last non-ok result is returned.
"""

from __future__ import annotations

from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines.base import Engine
from OpenSesame.api.engines.recaptcha_audio import RecaptchaAudioEngine
from OpenSesame.api.engines.recaptcha_grid import RecaptchaGridEngine
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelRegistry
from OpenSesame.api.result import Family, SolveResult, SolveStatus


class RecaptchaV2Engine:
    """Composes ordered reCAPTCHA v2 strategies (audio-first, grid fallback)."""

    family = Family.RECAPTCHA_V2

    def __init__(self, strategies: list[Engine]) -> None:
        self.strategies = strategies

    @classmethod
    def default(cls) -> "RecaptchaV2Engine":
        return cls([RecaptchaAudioEngine(), RecaptchaGridEngine()])

    def model_keys(self, policy: SolverPolicy) -> list:
        keys: list = []
        for strategy in self.strategies:
            mk = getattr(strategy, "model_keys", None)
            if mk:
                keys.extend(mk(policy))
        return keys

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
        # Audio is the preferred path for reCAPTCHA v2 (reliable local token mint);
        # the image grid is a fallback. Policy may pin a single strategy via
        # models["recaptcha_v2_strategy"] = "audio" | "grid".
        for strategy in self._ordered(policy):
            result = await strategy.solve(
                challenge, page, registry=registry, policy=policy, correlation_id=correlation_id,
            )
            if result.ok:
                return result
            # A rate-limit is a session/IP problem (downstream rotates proxy/profile);
            # don't burn a grid attempt on a throttled session — surface it.
            if result.status is SolveStatus.RATE_LIMITED:
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
