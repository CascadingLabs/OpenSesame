from __future__ import annotations

import asyncio
from pathlib import Path

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.engines import recaptcha_audio
from OpenSesame.api.engines.direct_answer import DirectAnswerEngine
from OpenSesame.api.engines.recaptcha import RecaptchaV2Engine
from OpenSesame.api.engines.recaptcha_audio import RecaptchaAudioEngine, normalize_answer
from OpenSesame.api.engines.recaptcha_grid import RecaptchaGridEngine, parse_target
from OpenSesame.api.engines.turnstile import TurnstileEngine
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelRegistry
from OpenSesame.api.result import (
    AnswerSolution,
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    TokenSolution,
)


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
    """Scripts the audio flow over the frame-scoped eval API (origin-agnostic)."""

    def __init__(self) -> None:
        self.verified = False
        self.clicked_audio = False

    async def eval_js(self, js: str):
        # Parent-document reads: only the minted token.
        if "g-recaptcha-response" in js:
            return "TOK-AUDIO" if self.verified else ""
        return None

    async def eval_js_in_frame(self, pattern: str, js: str):
        if "recaptcha-audio-button" in js and "click" in js:   # switch to audio
            self.clicked_audio = True
            return True
        if "recaptcha-verify-button" in js:                    # verify
            self.verified = True
            return True
        if "recaptcha-audio-button" in js:                     # open / ready probe
            return True
        if "rc-audiochallenge-tdownload-link" in js:           # audio state
            return {"download": "https://host/audio.mp3", "has_response": True, "rate_limited": False}
        if "audio-response" in js:                             # set response
            return True
        if "recaptcha-anchor" in js:                           # checkbox click
            return True
        return None

    async def frame_urls(self):
        return ["https://www.google.com/recaptcha/api2/bframe?k=demo"]


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
        async def eval_js_in_frame(self, pattern: str, js: str):
            if "rc-audiochallenge-tdownload-link" in js:
                return {"download": None, "has_response": True, "rate_limited": True}
            return await super().eval_js_in_frame(pattern, js)

    reg = ModelRegistry()
    reg.register_factory("whisper", lambda key: FakeTranscriber())
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://www.google.com/x", host="www.google.com")
    result = run(RecaptchaAudioEngine().solve(ch, RatePage(), registry=reg, policy=POLICY))
    assert result.status is SolveStatus.RATE_LIMITED


# -- enterprise + cross-origin via frame-scoped eval (voidcrawl 0.3.5) -----

def test_frame_patterns_cover_v2_and_enterprise() -> None:
    """One frame engine drives both v2 (api2) and Enterprise frames."""
    from OpenSesame.api.engines._recaptcha_dom import ANCHOR_PATTERNS, BFRAME_PATTERNS
    assert "api2/bframe" in BFRAME_PATTERNS and "enterprise/bframe" in BFRAME_PATTERNS
    assert "api2/anchor" in ANCHOR_PATTERNS and "enterprise/anchor" in ANCHOR_PATTERNS


class EnterpriseGridPage:
    """A cross-origin Enterprise widget: only the enterprise/* frame resolves."""

    def __init__(self) -> None:
        self.solved = False

    async def eval_js(self, js: str):
        if "g-recaptcha-response" in js:
            return "ENT-TOK"            # token already minted in the parent
        return None

    async def eval_js_in_frame(self, pattern: str, js: str):
        if "enterprise" not in pattern:   # the api2/* pattern misses → engine tries enterprise next
            raise RuntimeError("FrameNotFound")
        if "rc-imageselect" in js:        # challenge-open probe
            return True
        return None

    async def frame_urls(self):
        return ["https://www.google.com/recaptcha/enterprise/bframe?k=ent"]


def test_grid_drives_enterprise_cross_origin_frame() -> None:
    reg = ModelRegistry()
    reg.register_factory("tiles", lambda key: object())
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://shop.example/checkout",
                   host="shop.example", vendor_kind="recaptcha")
    result = run(RecaptchaGridEngine().solve(ch, EnterpriseGridPage(), registry=reg, policy=POLICY))
    assert result.ok and result.token == "ENT-TOK"   # reached the enterprise frame


class NoFramePage:
    """No reCAPTCHA frame on the page at all."""

    async def eval_js(self, js: str):
        return "" if "g-recaptcha-response" in js else None

    async def eval_js_in_frame(self, pattern: str, js: str):
        raise RuntimeError("FrameNotFound")

    async def frame_urls(self):
        return ["https://shop.example/checkout"]   # nothing recaptcha-shaped


class IsolatedFramePage(NoFramePage):
    """The frame exists (tracked) but is out-of-process — needs the isolation flag."""

    async def frame_urls(self):
        return ["https://www.google.com/recaptcha/api2/bframe?k=x"]


class OldVoidcrawlPage:
    """A VoidCrawl < 0.3.5 page: no eval_js_in_frame / frame_urls at all."""

    async def eval_js(self, js: str):
        return "" if "g-recaptcha-response" in js else None


def _grid_failure(page) -> SolveResult:
    reg = ModelRegistry()
    reg.register_factory("tiles", lambda key: object())
    ch = Challenge(family=Family.RECAPTCHA_V2, url="https://shop.example/x",
                   host="shop.example", vendor_kind="recaptcha")
    return run(RecaptchaGridEngine().solve(ch, page, registry=reg, policy=POLICY))


def test_grid_absent_frame_is_plain_failure() -> None:
    result = _grid_failure(NoFramePage())
    assert result.status is SolveStatus.FAILED
    assert result.metadata.get("reason") == "frame_absent"
    assert result.metadata.get("frame_isolated") is not True
    assert "no reCAPTCHA challenge frame" in result.error


def test_grid_isolated_frame_points_at_the_flag() -> None:
    result = _grid_failure(IsolatedFramePage())
    assert result.status is SolveStatus.FAILED
    assert result.metadata.get("reason") == "frame_isolated"
    assert result.metadata.get("frame_isolated") is True            # actionable, not vague
    assert "disable-site-isolation-trials" in result.error
    assert "disable-site-isolation-trials" in result.metadata["remediation"]


def test_grid_old_voidcrawl_says_upgrade() -> None:
    result = _grid_failure(OldVoidcrawlPage())
    assert result.status is SolveStatus.FAILED
    assert result.metadata.get("reason") == "voidcrawl_too_old"     # not mislabeled "absent"
    assert "voidcrawl>=0.3.5" in result.error


# -- Cloudflare Turnstile (AX-located checkbox + humanized click) ---------

class FakeTurnstilePage:
    """Scripts the Turnstile flow: AX-locate the checkbox, click, token mints."""

    def __init__(self, *, has_frame=True, checkbox_present=True,
                 preset_token="", mint_on_click=True,
                 interstitial=False, auto_clear=False) -> None:
        self.has_frame = has_frame
        self.checkbox_present = checkbox_present
        self._token = preset_token
        self.mint_on_click = mint_on_click
        self.interstitial = interstitial
        # The minimal-stealth browser auto-clears the managed challenge with no click.
        self.auto_clear = auto_clear
        self.clicked = False
        self.humanized = False

    async def eval_js(self, js: str):
        if "cf-turnstile-response" in js:
            return self._token
        if "just a moment" in js.lower():        # _is_interstitial probe (regex test)
            return self.interstitial
        if "document.title" in js:               # _cleared probe (title string)
            return "Just a moment..." if (self.interstitial and not self.auto_clear) \
                else "Demo page"
        return None

    async def detect_captcha(self):
        return None if self.auto_clear else "turnstile"

    async def frame_urls(self):
        present = self.has_frame and not self.auto_clear
        return ["https://challenges.cloudflare.com/cdn-cgi/c/x"] if present \
            else ["https://shop.example/checkout"]

    async def click_ax_in_frame(self, frame, role, name, nth=0, humanize=False):
        if not self.checkbox_present:
            raise RuntimeError("FrameNotFound")
        self.clicked = True
        self.humanized = humanize
        if self.mint_on_click:
            self._token = "0.TURNSTILE.TOKEN"


def test_turnstile_clicks_checkbox_and_harvests_token() -> None:
    page = FakeTurnstilePage()
    ch = Challenge(family=Family.TURNSTILE, url="https://shop.example/login",
                   host="shop.example", vendor_kind="turnstile")
    result = run(TurnstileEngine().solve(ch, page, registry=ModelRegistry(), policy=POLICY))
    assert result.ok and result.token == "0.TURNSTILE.TOKEN"
    assert result.solved_by is SolvedBy.LOCAL and result.vendor == "turnstile"
    # The checkbox was clicked via the AX locator with humanize requested.
    assert page.clicked and page.humanized is True


def test_turnstile_already_passed_returns_token() -> None:
    page = FakeTurnstilePage(preset_token="0.ALREADY.PASSED")
    ch = Challenge(family=Family.TURNSTILE, url="https://shop.example/x", host="shop.example")
    result = run(TurnstileEngine().solve(ch, page, registry=ModelRegistry(), policy=POLICY))
    assert result.ok and result.token == "0.ALREADY.PASSED"
    assert page.clicked is False     # no click needed


def test_turnstile_managed_challenge_clears_without_click() -> None:
    """The interstitial is cleared by the minimal-stealth browser — no click, no token."""
    page = FakeTurnstilePage(interstitial=True, auto_clear=True)
    ch = Challenge(family=Family.TURNSTILE, url="https://shop.example/x",
                   host="shop.example", vendor_kind="turnstile")
    result = run(TurnstileEngine().solve(ch, page, registry=ModelRegistry(), policy=POLICY))
    assert result.ok and result.token is None       # cleared, no token to mint
    assert result.metadata.get("cleared") is True
    assert page.clicked is False                     # the engine does NOT click here


def test_turnstile_managed_challenge_not_cleared_points_at_stealth() -> None:
    """An interstitial that never clears reports the CDP/stealth requirement, not 'no token'."""
    page = FakeTurnstilePage(interstitial=True, auto_clear=False)
    ch = Challenge(family=Family.TURNSTILE, url="https://shop.example/x", host="shop.example")
    engine = TurnstileEngine(clearance_tries=2)
    result = run(engine.solve(ch, page, registry=ModelRegistry(), policy=POLICY))
    assert result.status is SolveStatus.FAILED
    assert result.metadata.get("reason") == "managed_challenge"
    assert "VOIDCRAWL_STEALTH_NO_RUNTIME" in result.error and page.clicked is False


def test_turnstile_no_frame_is_failure() -> None:
    page = FakeTurnstilePage(has_frame=False)
    ch = Challenge(family=Family.TURNSTILE, url="https://shop.example/x", host="shop.example")
    result = run(TurnstileEngine().solve(ch, page, registry=ModelRegistry(), policy=POLICY))
    assert result.status is SolveStatus.FAILED
    assert "no Cloudflare Turnstile frame" in result.error


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
