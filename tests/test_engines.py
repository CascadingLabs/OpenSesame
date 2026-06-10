from __future__ import annotations

import asyncio
from pathlib import Path

from open_sesame.api.challenge import Challenge
from open_sesame.api.engines import recaptcha_audio
from open_sesame.api.engines.direct_answer import DirectAnswerEngine
from open_sesame.api.engines.recaptcha import RecaptchaV2Engine
from open_sesame.api.engines.recaptcha_audio import RecaptchaAudioEngine, normalize_answer
from open_sesame.api.engines.recaptcha_grid import parse_target
from open_sesame.api.policy import SolverPolicy
from open_sesame.api.registry import ModelRegistry
from open_sesame.api.result import AnswerSolution, Family, SolveResult, SolveStatus, TokenSolution


def run(coro):
    return asyncio.run(coro)


POLICY = SolverPolicy(allow_sites=["www.google.com"], audit_log=None)


# -- pure helpers ---------------------------------------------------------

def test_parse_target_extracts_object() -> None:
    assert parse_target("Select all squares with buses If there are none click skip") == "buses"
    assert parse_target("Select all images with a fire hydrant") == "fire hydrant"
    assert parse_target("Please verify") is None


def test_normalize_answer_strips_punct() -> None:
    assert normalize_answer("Hello, World!  ") == "hello world"


# -- audio engine end-to-end (scripted DOM) -------------------------------

class FakeAudioPage:
    """Scripts the same-origin bframe audio flow."""

    def __init__(self) -> None:
        self.verified = False
        self.clicked_audio = False

    async def eval_js(self, js: str):
        if "recaptcha-audio-button" in js:
            self.clicked_audio = True
            return True
        if "recaptcha-verify-button" in js:
            self.verified = True
            return True
        if "g-recaptcha-response" in js and "rc-audiochallenge" not in js and "audio-response" not in js:
            return "TOK-AUDIO" if self.verified else ""
        if "rc-audiochallenge-tdownload-link" in js:
            return {"ok": True, "download": "https://host/audio.mp3", "rate_limited": False, "token": ""}
        if "audio-response" in js:
            return True
        return None


class FakeTranscriber:
    model_id = "openai/whisper-base.en"
    device = "cpu"

    def transcribe(self, audio_path: str) -> str:
        return "hello world"


def test_audio_engine_solves(monkeypatch) -> None:
    # No real network: the download just touches the file.
    monkeypatch.setattr(recaptcha_audio, "_download", lambda url, out: Path(out).write_bytes(b"x"))
    reg = ModelRegistry()
    reg.register_factory("whisper", lambda key: FakeTranscriber())
    engine = RecaptchaAudioEngine()
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://www.google.com/recaptcha/api2/demo",
                   host="www.google.com", vendor_kind="recaptcha")

    result = run(engine.solve(ch, FakeAudioPage(), registry=reg, policy=POLICY))
    assert result.ok
    assert isinstance(result.solution, TokenSolution)
    assert result.token == "TOK-AUDIO"
    assert result.metadata["transcript"] == "hello world"
    assert reg.loaded_keys() == []  # acquired then released


def test_audio_engine_surfaces_rate_limit(monkeypatch) -> None:
    class RatePage(FakeAudioPage):
        async def eval_js(self, js: str):
            if "rc-audiochallenge-tdownload-link" in js:
                return {"ok": True, "download": None, "rate_limited": True, "token": ""}
            return await super().eval_js(js)

    reg = ModelRegistry()
    reg.register_factory("whisper", lambda key: FakeTranscriber())
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://www.google.com/x", host="www.google.com")
    result = run(RecaptchaAudioEngine().solve(ch, RatePage(), registry=reg, policy=POLICY))
    assert result.status is SolveStatus.RATE_LIMITED


# -- OCR engine -----------------------------------------------------------

class FakeReader:
    model_id = "grafj-conv-transformer-base"
    device = "cpu"

    def read_text(self, image_path: str) -> tuple[str, float]:
        return "AB12CD", 0.91


def test_ocr_engine_returns_answer(tmp_path) -> None:
    img = tmp_path / "cap.png"
    img.write_bytes(b"x")
    reg = ModelRegistry()
    reg.register_factory("ocr", lambda key: FakeReader())
    ch = Challenge.ocr(url="https://site.test/login", image_path=str(img))
    object.__setattr__(ch, "host", "site.test")

    result = run(DirectAnswerEngine().solve(ch, object(), registry=reg, policy=POLICY))
    assert result.ok
    assert isinstance(result.solution, AnswerSolution)
    assert result.answer == "AB12CD"
    assert result.confidence == 0.91


# -- composite ordering ---------------------------------------------------

class StratFail:
    family = Family.RECAPTCHA_V2

    async def solve(self, ch, page, *, registry, policy, correlation_id=None):
        return SolveResult(status=SolveStatus.FAILED, family=ch.family, error="audio failed",
                           metadata={"strategy": "audio"})


class StratOK:
    family = Family.RECAPTCHA_V2

    async def solve(self, ch, page, *, registry, policy, correlation_id=None):
        return SolveResult(status=SolveStatus.SOLVED, family=ch.family,
                           solution=TokenSolution("grid-tok"), metadata={"strategy": "grid"})


def test_composite_falls_through_to_second_strategy() -> None:
    engine = RecaptchaV2Engine([StratFail(), StratOK()])
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://www.google.com/x", host="www.google.com")
    result = run(engine.solve(ch, object(), registry=ModelRegistry(), policy=POLICY))
    assert result.ok and result.token == "grid-tok"
