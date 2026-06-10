"""Policy-as-data: the one declarative door for configuring a Solver.

A value belongs in policy if you'd diff it across runs to explain a behavior
change (allowed sites, timeouts, model choices, rate limits). The challenge
instance, the live page, callbacks, and correlation ids stay imperative
``solve()`` arguments; they are not policy.

Policy is frozen + ``extra="forbid"`` so a typo'd TOML key fails loud instead of
silently doing nothing. Responsible-use is structural: ``allow_sites`` is
default-deny (empty = solve nothing), enforced fail-closed by the Solver.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SiteNotAllowed(Exception):
    """Raised (loudly) when a host is not in the policy's allow_sites."""

    def __init__(self, host: str) -> None:
        self.host = host
        super().__init__(
            f"host {host!r} is not in allow_sites; add it to the policy to solve here "
            "(allow_sites is default-deny)."
        )


class SolverPolicy(BaseModel):
    """Declarative configuration for a :class:`~OpenSesame.api.solver.Solver`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Responsible-use: default-deny. Empty => solve nothing. Hostnames, exact match.
    allow_sites: tuple[str, ...] = ()

    # Three distinct timeout regimes (seconds).
    auto_timeout_s: float = 30.0       # local-model inference path
    manual_timeout_s: float = 300.0    # human-in-the-loop path
    queue_timeout_s: float = 10.0      # max wait to enter the worker pool

    # Apply the solution to the live page on success; the default. OpenSesame
    # already drives the page to solve, so it resolves the token into the DOM/CDP
    # (or types the answer) itself: callers just check ``result.ok``, no inject
    # step. Set ``apply = false`` for the narrower over-the-wire case where you
    # want the raw token/answer (``result.token`` / ``result.answer``) to inject
    # into a different session or relay yourself.
    apply: bool = True

    min_confidence: float = 0.0        # below this, an auto solve is FAILED (and may escalate)
    device: str = "auto"               # opaque; resolved by the model registry
    models: dict[str, str] = Field(default_factory=dict)  # family -> opaque model_id override

    rate_limit_per_host_s: float = 0.0     # min seconds between solves to one host (0 = off)
    escalate_on_fail: bool = False         # auto FAILED/low-confidence -> manual path

    audit_log: str | None = ".local/opensesame-audit.jsonl"   # provenance jsonl; None disables
    notify_uri: str | None = None          # single URI for manual-queue notifications (YAGNI: no object)

    policy_id: str = "default"             # label that lands in every audit record

    @classmethod
    def auto_only(cls, **overrides: Any) -> SolverPolicy:
        """Local-models-only policy (no human escalation)."""

        return cls(escalate_on_fail=False, **overrides)

    @classmethod
    def from_toml(cls, path: str | Path) -> SolverPolicy:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def merged(self, **overrides: Any) -> SolverPolicy:
        """Per-call precedence: return a copy with overrides applied.

        Lists replace; dict fields (``models``) deep-merge so a per-call model
        pin does not wipe the global table.
        """

        if not overrides:
            return self
        base = self.model_dump()
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = {**base[key], **value}
            else:
                base[key] = value
        return SolverPolicy.model_validate(base)

    def allows(self, host: str) -> bool:
        return host in self.allow_sites


def load_policy(
    path: str | Path | None = None,
    *,
    host_dir: str | Path | None = None,
    host: str | None = None,
    **overrides: Any,
) -> SolverPolicy:
    """Resolve a policy with precedence global -> per-host file -> per-call overrides.

    - ``path``: the global ``opensesame.toml`` (or defaults if None).
    - ``host_dir``: a directory of per-host TOML files (``<host>.toml``); the file
      for ``host`` is merged over the global. A flat directory, not a nested tree.
    - ``overrides``: per-call keyword overrides, highest precedence.
    """

    policy = SolverPolicy.from_toml(path) if path else SolverPolicy()
    if host_dir and host:
        host_file = Path(host_dir) / f"{host}.toml"
        if host_file.exists():
            host_data = tomllib.loads(host_file.read_text(encoding="utf-8"))
            policy = policy.merged(**host_data)
    return policy.merged(**overrides)
