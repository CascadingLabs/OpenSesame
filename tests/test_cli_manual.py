from __future__ import annotations

import asyncio

from click.testing import CliRunner

from open_sesame.api.challenge import Challenge
from open_sesame.api.defaults import default_solver
from open_sesame.api.manual import CallbackManualSolver
from open_sesame.api.policy import SolverPolicy
from open_sesame.api.result import Family, SolvedBy, SolveResult, SolveStatus, TokenSolution
from open_sesame.cli import cli


def run(coro):
    return asyncio.run(coro)


def test_check_reports_policy_and_engines() -> None:
    result = CliRunner().invoke(cli, ["check"])
    assert result.exit_code == 0, result.output
    assert "policy OK" in result.output
    assert "recaptcha_v2" in result.output
    assert "ocr" in result.output
    # API-only: providers absent -> flagged, with default-deny note.
    assert "default-deny" in result.output


def test_check_rejects_invalid_policy(tmp_path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("allow_sties = ['x']\n", encoding="utf-8")  # typo'd key
    result = CliRunner().invoke(cli, ["check", "--policy", str(bad)])
    assert result.exit_code == 1
    assert "policy invalid" in result.output


def test_download_rejects_unknown_kind() -> None:
    result = CliRunner().invoke(cli, ["download", "bogus"])
    assert result.exit_code != 0


def test_callback_manual_solver_escalation() -> None:
    async def human(challenge, page, timeout_s, correlation_id):
        return SolveResult(
            status=SolveStatus.SOLVED, family=challenge.family,
            solution=TokenSolution("human-token"), solved_by=SolvedBy.HUMAN,
        )

    policy = SolverPolicy(allow_sites=["www.google.com"], escalate_on_fail=True, audit_log=None)
    solver = default_solver(policy, manual=CallbackManualSolver(human))
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://www.google.com/x", host="www.google.com")

    # No model providers installed -> engine raises LookupError -> FAILED -> escalates to human.
    result = run(solver.solve(ch, object()))
    assert result.ok
    assert result.solved_by is SolvedBy.HUMAN
    assert result.token == "human-token"
