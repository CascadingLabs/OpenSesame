"""The one public door: ``Solver(policy=...)``.

Auto (local models) and manual (humans) are different machines — different
latency regimes, failure models, and scaling axes. They are split behind one
async **ticket** abstraction so neither leaks into the caller and so V2 can swap
the in-process future for a Redis/RQ queue without changing a single call site:

    ticket = await solver.submit(challenge, page)     # returns immediately
    result = await solver.await_result(ticket)        # SolveResult (failure-as-value)
    result = await solver.solve(challenge, page)      # sugar over the two

Responsible-use is structural: ``allow_sites`` is fail-closed (a denied host
raises ``SiteNotAllowed`` — the one place we raise instead of returning a value).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from OpenSesame.api.audit import AuditLog
from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines.base import Engine, ManualSolver
from OpenSesame.api.policy import SiteNotAllowed, SolverPolicy
from OpenSesame.api.registry import ModelRegistry, default_registry
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    Timing,
)


@dataclass
class Ticket:
    """Handle to an in-flight solve. Backed by an asyncio task in v1."""

    id: str
    challenge: Challenge
    submitted_at: float
    _task: asyncio.Task[SolveResult]

    @property
    def done(self) -> bool:
        return self._task.done()


@dataclass
class _Clock:
    """Injectable time source so tests don't sleep on real wall-clock."""

    monotonic: Any = field(default=time.monotonic)
    wall: Any = field(default=time.time)


class Solver:
    """Routes a challenge to the right local engine; escalates to a human on policy."""

    def __init__(
        self,
        policy: SolverPolicy,
        *,
        registry: ModelRegistry | None = None,
        engines: dict[Family, Engine] | None = None,
        manual: ManualSolver | None = None,
        max_concurrency: int = 4,
        clock: _Clock | None = None,
    ) -> None:
        self.policy = policy
        self.registry = registry or default_registry()
        self.audit = AuditLog(policy.audit_log)
        self._engines: dict[Family, Engine] = dict(engines or {})
        self._manual = manual
        self._sema = asyncio.Semaphore(max_concurrency)
        self._clock = clock or _Clock()
        self._last_solve_per_host: dict[str, float] = {}
        self._counter = 0

    # -- registration -----------------------------------------------------

    def register_engine(self, family: Family, engine: Engine) -> None:
        self._engines[family] = engine

    def register_manual(self, manual: ManualSolver) -> None:
        self._manual = manual

    # -- lifecycle --------------------------------------------------------

    @contextlib.asynccontextmanager
    async def engine(self, *, warmup: list[Any] | None = True):
        """Optional model-lifetime scope for a block of solves.

        Not required: ``await solver.solve(...)`` loads its model on first use and
        keeps it cached. ``engine()`` just pre-warms the models the policy implies
        (so a cold first call doesn't eat the solve timeout) and unloads them on
        exit for deterministic VRAM cleanup. ``warmup=True`` (default) derives the
        keys from the policy + registered engines; pass an explicit key list to
        override, or ``warmup=False`` / ``None`` to skip warming.
        """

        if warmup is True:
            keys = self._policy_model_keys()
        elif warmup in (False, None):
            keys = []
        else:
            keys = list(warmup)
        loaded = self.registry.warmup(keys)
        try:
            yield self
        finally:
            for key in loaded:
                self.registry.unload(key)

    def _policy_model_keys(self) -> list[Any]:
        """Models the registered engines need under the current policy."""

        keys: list[Any] = []
        seen: set[Any] = set()
        for engine in self._engines.values():
            mk = getattr(engine, "model_keys", None)
            if mk is None:
                continue
            for key in mk(self.policy):
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        return keys

    def warmup(self) -> list[Any]:
        """Eagerly load the policy's models now (returns the keys loaded)."""

        return self.registry.warmup(self._policy_model_keys())

    # -- the ticket abstraction ------------------------------------------

    def submit(
        self,
        challenge: Challenge,
        page: Any,
        *,
        correlation_id: str | None = None,
        **overrides: Any,
    ) -> Ticket:
        """Validate + enqueue a solve; return a ticket immediately.

        Raises ``SiteNotAllowed`` (misconfiguration, fail-closed) — the only
        non-value failure. Everything else surfaces as a ``SolveResult``.
        """

        policy = self.policy.merged(**overrides) if overrides else self.policy
        if not policy.allows(challenge.host):
            raise SiteNotAllowed(challenge.host)

        self._counter += 1
        ticket_id = f"os-{self._counter}"
        submitted = self._clock.wall()
        coro = self._run(challenge, page, policy, correlation_id)
        task = asyncio.ensure_future(coro)
        return Ticket(id=ticket_id, challenge=challenge, submitted_at=submitted, _task=task)

    async def await_result(self, ticket: Ticket, *, timeout: float | None = None) -> SolveResult:
        """Await a ticket; a clean timeout is a ``SolveResult`` value, not a raise."""

        limit = timeout if timeout is not None else self.policy.auto_timeout_s
        try:
            done, _ = await asyncio.wait({ticket._task}, timeout=limit)
        except asyncio.CancelledError:
            ticket._task.cancel()
            raise
        if ticket._task in done:
            return ticket._task.result()
        # Timed out: leave the task running (cancelling a CDP op can wedge the
        # page), but report a TIMEOUT value to the caller.
        return self._terminal(
            ticket.challenge,
            SolveStatus.TIMEOUT,
            correlation_id=None,
            started=ticket.submitted_at,
            error=f"await_result exceeded {limit:.1f}s",
        )

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        timeout: float | None = None,
        correlation_id: str | None = None,
        **overrides: Any,
    ) -> SolveResult:
        """Sugar: submit + await_result."""

        ticket = self.submit(challenge, page, correlation_id=correlation_id, **overrides)
        return await self.await_result(ticket, timeout=timeout)

    # -- internals --------------------------------------------------------

    async def _run(
        self,
        challenge: Challenge,
        page: Any,
        policy: SolverPolicy,
        correlation_id: str | None,
    ) -> SolveResult:
        started = self._clock.wall()

        # Per-host rate limit: surface as a value, let downstream rotate.
        if policy.rate_limit_per_host_s > 0:
            last = self._last_solve_per_host.get(challenge.host)
            now = self._clock.monotonic()
            if last is not None and (now - last) < policy.rate_limit_per_host_s:
                return self._terminal(
                    challenge, SolveStatus.RATE_LIMITED, correlation_id, started,
                    policy=policy, error="per-host rate limit",
                )
            self._last_solve_per_host[challenge.host] = now

        engine = self._engines.get(challenge.family)
        if engine is None:
            return self._terminal(
                challenge, SolveStatus.FAILED, correlation_id, started,
                policy=policy, error=f"no engine registered for family {challenge.family.value}",
            )

        # Bounded worker pool: queue_timeout to *enter* is distinct from solve time.
        try:
            await asyncio.wait_for(self._sema.acquire(), timeout=policy.queue_timeout_s)
        except (TimeoutError, asyncio.TimeoutError):
            return self._terminal(
                challenge, SolveStatus.TIMEOUT, correlation_id, started,
                policy=policy, error="queue timeout (worker pool full)",
            )
        try:
            result = await self._run_engine(engine, challenge, page, policy, correlation_id, started)
        finally:
            self._sema.release()

        # Escalate to a human if the local attempt failed/under-confident.
        if self._should_escalate(result, policy):
            result = await self._run_manual(challenge, page, policy, correlation_id, started)

        # Apply the solution to the live page by default (token injected / answer
        # typed). policy.apply=False leaves it for the caller (over-the-wire case).
        if result.ok and policy.apply:
            result = await self._apply(result, challenge, page)

        self.audit.record(result, now=self._clock.wall())
        return result

    async def _apply(self, result: SolveResult, challenge: Challenge, page: Any) -> SolveResult:
        """Resolve the solution into the live page (best-effort)."""

        from dataclasses import replace

        solution = result.solution
        applied = False
        try:
            if solution is not None and solution.is_token:
                inject = getattr(page, "inject_captcha_token", None)
                if callable(inject):
                    await inject(solution.token)
                applied = True   # reCAPTCHA token is already in the page from the live solve
            elif solution is not None and solution.is_answer and challenge.response_field_selector:
                applied = await _type_answer(page, challenge.response_field_selector, solution.text)
        except Exception:
            applied = False
        return replace(result, applied=applied)

    async def _run_engine(
        self,
        engine: Engine,
        challenge: Challenge,
        page: Any,
        policy: SolverPolicy,
        correlation_id: str | None,
        started: float,
    ) -> SolveResult:
        try:
            result = await asyncio.wait_for(
                engine.solve(
                    challenge, page,
                    registry=self.registry, policy=policy, correlation_id=correlation_id,
                ),
                timeout=policy.auto_timeout_s,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return self._terminal(
                challenge, SolveStatus.TIMEOUT, correlation_id, started,
                policy=policy, error=f"engine exceeded auto_timeout_s={policy.auto_timeout_s}",
            )
        except Exception as exc:  # engines surface failures as values; this is the backstop
            return self._terminal(
                challenge, SolveStatus.FAILED, correlation_id, started,
                policy=policy, error=f"{type(exc).__name__}: {exc}",
            )
        # Confidence gate: a low-confidence local solve is a FAILED (may escalate).
        if (
            result.ok
            and result.confidence is not None
            and result.confidence < policy.min_confidence
        ):
            return self._terminal(
                challenge, SolveStatus.FAILED, correlation_id, started,
                policy=policy, solved_by=SolvedBy.LOCAL,
                error=f"confidence {result.confidence:.3f} < min_confidence {policy.min_confidence}",
            )
        return self._stamp(result, challenge, policy, correlation_id, started)

    def _should_escalate(self, result: SolveResult, policy: SolverPolicy) -> bool:
        return (
            policy.escalate_on_fail
            and self._manual is not None
            and result.status in {SolveStatus.FAILED, SolveStatus.TIMEOUT}
        )

    async def _run_manual(
        self,
        challenge: Challenge,
        page: Any,
        policy: SolverPolicy,
        correlation_id: str | None,
        started: float,
    ) -> SolveResult:
        assert self._manual is not None
        try:
            result = await asyncio.wait_for(
                self._manual.solve(
                    challenge, page,
                    timeout_s=policy.manual_timeout_s, correlation_id=correlation_id,
                ),
                timeout=policy.manual_timeout_s + 1.0,
            )
            return self._stamp(result, challenge, policy, correlation_id, started)
        except (TimeoutError, asyncio.TimeoutError):
            return self._terminal(
                challenge, SolveStatus.TIMEOUT, correlation_id, started,
                policy=policy, solved_by=SolvedBy.HUMAN, error="manual timeout",
            )
        except Exception as exc:
            return self._terminal(
                challenge, SolveStatus.FAILED, correlation_id, started,
                policy=policy, solved_by=SolvedBy.HUMAN, error=f"manual: {type(exc).__name__}: {exc}",
            )

    def _stamp(
        self,
        result: SolveResult,
        challenge: Challenge,
        policy: SolverPolicy,
        correlation_id: str | None,
        started: float,
    ) -> SolveResult:
        """Fill Solver-owned context an engine need not know (host, policy, timing)."""

        from dataclasses import replace

        return replace(
            result,
            host=result.host or challenge.host,
            vendor=result.vendor or challenge.vendor_kind,
            policy_id=result.policy_id or policy.policy_id,
            correlation_id=result.correlation_id or correlation_id,
            timing=result.timing
            or Timing(started_at=started, elapsed_ms=(self._clock.wall() - started) * 1000.0),
        )

    def _terminal(
        self,
        challenge: Challenge,
        status: SolveStatus,
        correlation_id: str | None,
        started: float,
        *,
        policy: SolverPolicy | None = None,
        solved_by: SolvedBy | None = None,
        error: str = "",
    ) -> SolveResult:
        pol = policy or self.policy
        return SolveResult(
            status=status,
            family=challenge.family,
            host=challenge.host,
            vendor=challenge.vendor_kind,
            solved_by=solved_by,
            policy_id=pol.policy_id,
            correlation_id=correlation_id,
            timing=Timing(started_at=started, elapsed_ms=(self._clock.wall() - started) * 1000.0),
            error=error,
        )


async def _type_answer(page: Any, selector: str, text: str) -> bool:
    """Type an OCR answer into a form field with real CDP keystrokes.

    A plain JS value-set does not trigger some forms' validation; typing via the
    page's ``type_into`` (CDP keys) does. Falls back to a DOM value-set action.
    """

    type_into = getattr(page, "type_into", None)
    if callable(type_into):
        try:
            import json

            await page.eval_js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(e)e.value='';}})()")
            await type_into(selector, text)
            return True
        except Exception:
            pass
    from OpenSesame.api.engines.direct_answer import fill_via_actions

    await fill_via_actions(page, selector, text)
    return True
