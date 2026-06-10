from __future__ import annotations

import asyncio
from pathlib import Path

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines import recaptcha_audio
from OpenSesame.api.engines.direct_answer import DirectAnswerEngine
from OpenSesame.api.engines.recaptcha import RecaptchaV2Engine
from OpenSesame.api.engines.recaptcha_audio import RecaptchaAudioEngine, normalize_answer
from OpenSesame.api.engines.recaptcha_grid import RecaptchaGridEngine, parse_target
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelRegistry
from OpenSesame.api.result import AnswerSolution, Family, SolveResult, SolveStatus, TokenSolution


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
    assert len(reg.loaded_keys()) == 1  # loaded once, cached for the process


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


# -- enterprise selectors + honest cross-origin signal --------------------

def test_engines_match_enterprise_frames() -> None:
    """v2 + Enterprise share challenge DOM; selectors must match both bframes."""
    from OpenSesame.api.engines import recaptcha_grid
    for js in (recaptcha_audio._AUDIO_STATE, recaptcha_grid._GRID_STATE):
        assert 'src*="api2/bframe"' in js
        assert 'src*="enterprise/bframe"' in js


class CrossOriginGridPage:
    """A real third-party site: the bframe is present but cross-origin."""

    async def eval_js(self, js: str):
        if "rc-imageselect-instructions" in js:        # _GRID_STATE
            return {"ok": False, "reason": "cross-origin"}
        if "#rc-imageselect'" in js:                   # _open_challenge "already open?" probe
            return True                                # skip the open loop
        return None


class NoFrameGridPage(CrossOriginGridPage):
    async def eval_js(self, js: str):
        if "rc-imageselect-instructions" in js:
            return {"ok": False, "reason": "no-frame"}
        if "#rc-imageselect'" in js:
            return True
        return None


def test_grid_cross_origin_is_honest_failure() -> None:
    reg = ModelRegistry()
    reg.register_factory("tiles", lambda key: object())
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://shop.example/checkout",
                   host="shop.example", vendor_kind="recaptcha")
    result = run(RecaptchaGridEngine().solve(ch, CrossOriginGridPage(), registry=reg, policy=POLICY))
    assert result.status is SolveStatus.FAILED
    assert result.metadata.get("cross_origin") is True     # machine-detectable, not vague
    assert "coordinate engine" in result.error


def test_grid_no_frame_is_plain_failure_not_cross_origin() -> None:
    reg = ModelRegistry()
    reg.register_factory("tiles", lambda key: object())
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://shop.example/checkout",
                   host="shop.example", vendor_kind="recaptcha")
    result = run(RecaptchaGridEngine().solve(ch, NoFrameGridPage(), registry=reg, policy=POLICY))
    assert result.status is SolveStatus.FAILED
    assert "cross_origin" not in result.metadata           # no frame at all != cross-origin


# -- OCR engine -----------------------------------------------------------

class FakeReader:
    model_id = "grafj-conv-transformer-base"
    device = "cpu"

    def read_text(self, image_path: str) -> tuple[str, float]:
        return "AB12CD", 0.91


def test_challenge_ocr_sets_selectors() -> None:
    ch = Challenge.ocr(
        url="https://s.test/login",
        image_selector="#cap",
        response_field_selector="#ans",
        capture_to="/tmp/c.png",
    )
    assert ch.family is Family.OCR and ch.host == "s.test"
    assert ch.response_field_selector == "#ans"        # field, so the Solver applies it
    assert ch.metadata["image_selector"] == "#cap"
    assert ch.metadata["capture_to"] == "/tmp/c.png"


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


def test_composite_short_circuits_on_audio_rate_limit() -> None:
    """A rate-limit (session/IP) shouldn't burn a grid attempt — surface it."""

    class StratRateLimited:
        family = Family.RECAPTCHA_V2
        tried = False

        async def solve(self, ch, page, *, registry, policy, correlation_id=None):
            return SolveResult(status=SolveStatus.RATE_LIMITED, family=ch.family)

    grid = StratOK()
    engine = RecaptchaV2Engine([StratRateLimited(), grid])
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://www.google.com/x", host="www.google.com")
    result = run(engine.solve(ch, object(), registry=ModelRegistry(), policy=POLICY))
    assert result.status is SolveStatus.RATE_LIMITED   # did not fall through to the grid
