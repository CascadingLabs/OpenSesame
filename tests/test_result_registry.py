from __future__ import annotations

import pytest

from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    AnswerSolution,
    Delivery,
    Family,
    SolveResult,
    SolveStatus,
    TokenSolution,
)


def test_token_solution_is_token() -> None:
    sol = TokenSolution(token="abc")
    assert sol.is_token and not sol.is_answer
    assert sol.delivery is Delivery.TOKEN_GRANT
    assert sol.value == "abc"


def test_answer_solution_is_answer() -> None:
    sol = AnswerSolution(text="42")
    assert sol.is_answer and not sol.is_token
    assert sol.delivery is Delivery.ANSWER


def test_solveresult_ok_and_accessors() -> None:
    ok = SolveResult(status=SolveStatus.SOLVED, family=Family.RECAPTCHA_V2, solution=TokenSolution("t"))
    assert ok.ok and ok.token == "t" and ok.answer is None
    assert ok.delivery is Delivery.TOKEN_GRANT

    fail = SolveResult(status=SolveStatus.FAILED, family=Family.OCR)
    assert not fail.ok and fail.solution is None and fail.token is None


def test_registry_loads_once_and_refcounts() -> None:
    calls: list[ModelKey] = []

    class Provider:
        def __init__(self) -> None:
            self.unloaded = False

        def unload(self) -> None:
            self.unloaded = True

    def factory(key: ModelKey) -> Provider:
        calls.append(key)
        return Provider()

    reg = ModelRegistry()
    reg.register_factory("whisper", factory)
    key = ModelKey(kind="whisper", model_id="base.en", device="cpu")

    p1 = reg.acquire(key)
    p2 = reg.acquire(key)
    assert p1 is p2
    assert len(calls) == 1                 # loaded once despite two acquires

    reg.release(key)
    assert reg.loaded_keys() == [key]      # still held by second acquire
    assert p1.unloaded is False
    reg.release(key)
    assert reg.loaded_keys() == []         # last release unloads
    assert p1.unloaded is True


def test_registry_missing_factory_raises() -> None:
    reg = ModelRegistry()
    with pytest.raises(LookupError):
        reg.acquire(ModelKey(kind="nope", model_id="x"))
