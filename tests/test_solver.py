from __future__ import annotations

import asyncio
import json

import pytest

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.policy import SiteNotAllowed, SolverPolicy
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    TokenSolution,
)
from OpenSesame.api.solver import Solver

PAGE = object()  # engines are faked; the page is opaque here


def challenge(host: str = "www.google.com") -> Challenge:
    return Challenge(family=Family.RECAPTCHA_V2, url=f"https://{host}/x", host=host)


class FakeEngine:
    def __init__(self, result: SolveResult | None = None, *, sleep: float = 0.0, raises: bool = False):
        self.result = result
        self.sleep = sleep
        self.raises = raises
        self.calls = 0

    async def solve(self, ch, page, *, registry, policy, correlation_id=None):
        self.calls += 1
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.raises:
            raise RuntimeError("boom")
        return self.result


def solved(conf: float | None = None) -> SolveResult:
    return SolveResult(
        status=SolveStatus.SOLVED, family=Family.RECAPTCHA_V2,
        solution=TokenSolution("tok"), solved_by=SolvedBy.LOCAL, confidence=conf,
    )


def run(coro):
    return asyncio.run(coro)


def test_allow_sites_fail_closed_raises() -> None:
    solver = Solver(SolverPolicy(allow_sites=["allowed.com"], audit_log=None))
    async def go():
        return solver.submit(challenge("denied.com"), PAGE)
    with pytest.raises(SiteNotAllowed):
        run(go())


def test_solve_success_routes_to_engine() -> None:
    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], audit_log=None))
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(solved()))
    result = run(solver.solve(challenge(), PAGE))
    assert result.ok and result.token == "tok" and result.host == "www.google.com"


def test_no_engine_is_failed_value_not_raise() -> None:
    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], audit_log=None))
    result = run(solver.solve(challenge(), PAGE))
    assert result.status is SolveStatus.FAILED
    assert "no engine" in result.error


@pytest.mark.parametrize(
    "family",
    [Family.RECAPTCHA_V3, Family.HCAPTCHA, Family.TURNSTILE],
)
def test_out_of_scope_family_is_refused_and_routed(family) -> None:
    """v3/hCaptcha/Turnstile are detect-and-route, not a generic 'no engine' miss."""
    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], audit_log=None))
    ch = Challenge(family=family, url="https://www.google.com/x", host="www.google.com")
    result = run(solver.solve(ch, PAGE))
    assert result.status is SolveStatus.REFUSED
    assert result.metadata.get("route") == "anti-bot"
    assert "no engine" not in result.error      # a clear reason, not the generic miss


def test_engine_timeout_is_value() -> None:
    policy = SolverPolicy(allow_sites=["www.google.com"], auto_timeout_s=0.05, audit_log=None)
    solver = Solver(policy)
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(solved(), sleep=1.0))
    result = run(solver.solve(challenge(), PAGE, timeout=5.0))
    assert result.status is SolveStatus.TIMEOUT


def test_engine_exception_is_failed_value() -> None:
    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], audit_log=None))
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(raises=True))
    result = run(solver.solve(challenge(), PAGE))
    assert result.status is SolveStatus.FAILED and "boom" in result.error


def test_low_confidence_is_failed() -> None:
    policy = SolverPolicy(allow_sites=["www.google.com"], min_confidence=0.8, audit_log=None)
    solver = Solver(policy)
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(solved(conf=0.4)))
    result = run(solver.solve(challenge(), PAGE))
    assert result.status is SolveStatus.FAILED and "confidence" in result.error


def test_escalation_to_manual_on_fail() -> None:
    class ManualOK:
        async def solve(self, ch, page, *, timeout_s, correlation_id=None):
            return SolveResult(
                status=SolveStatus.SOLVED, family=ch.family,
                solution=TokenSolution("human-tok"), solved_by=SolvedBy.HUMAN,
            )

    policy = SolverPolicy(allow_sites=["www.google.com"], escalate_on_fail=True, audit_log=None)
    solver = Solver(policy, manual=ManualOK())
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(raises=True))
    result = run(solver.solve(challenge(), PAGE))
    assert result.ok and result.solved_by is SolvedBy.HUMAN and result.token == "human-tok"


def test_rate_limit_is_value() -> None:
    policy = SolverPolicy(allow_sites=["www.google.com"], rate_limit_per_host_s=60.0, audit_log=None)
    solver = Solver(policy)
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(solved()))

    async def go():
        first = await solver.solve(challenge(), PAGE)
        second = await solver.solve(challenge(), PAGE)
        return first, second

    first, second = run(go())
    assert first.ok
    assert second.status is SolveStatus.RATE_LIMITED


def test_submit_returns_immediately_then_await() -> None:
    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], audit_log=None))
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(solved()))

    async def go():
        ticket = solver.submit(challenge(), PAGE)
        assert ticket.id.startswith("os-")
        return await solver.await_result(ticket, timeout=5.0)

    assert run(go()).ok


class InjectablePage:
    def __init__(self) -> None:
        self.injected: str | None = None

    async def inject_captcha_token(self, token: str) -> None:
        self.injected = token


def test_solve_applies_token_to_page_by_default() -> None:
    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], audit_log=None))
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(solved()))
    page = InjectablePage()
    result = run(solver.solve(challenge(), page))
    assert result.ok and result.applied is True
    assert page.injected == "tok"          # auto-injected, no caller step


def test_apply_false_leaves_page_untouched() -> None:
    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], apply=False, audit_log=None))
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(solved()))
    page = InjectablePage()
    result = run(solver.solve(challenge(), page))
    assert result.ok and result.applied is False
    assert page.injected is None           # over-the-wire: caller injects result.token
    assert result.token == "tok"


def test_engine_context_autowarms_from_policy() -> None:
    loaded: list = []

    class WarmEngine:
        family = Family.RECAPTCHA_V2

        def model_keys(self, policy):
            from OpenSesame.api.registry import ModelKey
            return [ModelKey("whisper", "openai/whisper-base.en", policy.device)]

        async def solve(self, ch, page, *, registry, policy, correlation_id=None):
            return solved()

    from OpenSesame.api.registry import ModelRegistry

    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], audit_log=None),
                    registry=ModelRegistry())  # isolated: the default registry is process-shared
    solver.register_engine(Family.RECAPTCHA_V2, WarmEngine())
    solver.registry.register_factory("whisper", lambda key: loaded.append(key) or object())

    async def go():
        async with solver.engine():        # no args: warms from policy + engines
            assert len(solver.registry.loaded_keys()) == 1
            return await solver.solve(challenge(), InjectablePage())

    result = run(go())
    assert result.ok
    assert len(loaded) == 1                 # warmed exactly once
    assert solver.registry.loaded_keys() == []  # unloaded on engine() exit


def test_audit_record_written(tmp_path) -> None:
    log = tmp_path / "audit.jsonl"
    solver = Solver(SolverPolicy(allow_sites=["www.google.com"], audit_log=str(log)))
    solver.register_engine(Family.RECAPTCHA_V2, FakeEngine(solved()))
    run(solver.solve(challenge(), PAGE))
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["host"] == "www.google.com"
    assert rec["status"] == "solved"
    assert rec["method"] == "local"
