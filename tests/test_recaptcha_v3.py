from __future__ import annotations

import asyncio

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.defaults import default_solver
from OpenSesame.api.engines.recaptcha_v3 import RecaptchaV3Engine, is_recaptcha_v3
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelRegistry
from OpenSesame.api.result import Family, SolveStatus
from OpenSesame.api.solver import _OUT_OF_SCOPE_ROUTES


def run(coro):
    return asyncio.run(coro)


POLICY = SolverPolicy(allow_sites=["2captcha.com"], audit_log=None)
REG = ModelRegistry()


# -- pure classifier ------------------------------------------------------

def test_is_v3_true_when_sitekey_and_no_widget() -> None:
    assert is_recaptcha_v3({"sitekey": "6Lc_x", "widget": False}) is True


def test_is_v3_false_when_v2_widget_present() -> None:
    # A v2 checkbox/anchor widget is present -> not v3, even with a sitekey.
    assert is_recaptcha_v3({"sitekey": "6Lc_x", "widget": True}) is False


def test_is_v3_false_without_sitekey_or_bad_input() -> None:
    assert is_recaptcha_v3({"sitekey": None, "widget": False}) is False
    assert is_recaptcha_v3({}) is False
    assert is_recaptcha_v3(None) is False


# -- engine over a scripted page -----------------------------------------

class FakeV3Page:
    """Scripts the v3 discover + mint over eval_js; records mouse interaction."""

    def __init__(self, *, enterprise=False, sitekey="6Lc_sitekey", action="demo_action",
                 widget=False, token="TOK-V3", mint_ok=True, ready=True) -> None:
        self.enterprise = enterprise
        self.sitekey = sitekey
        self.action = action
        self.widget = widget
        self.token = token
        self.mint_ok = mint_ok
        self.ready = ready
        self.moves: list[tuple] = []
        self.clicks: list[tuple] = []
        self.mint_js: str | None = None

    async def eval_js(self, js: str):
        if "script[src]" in js or "___grecaptcha_cfg" in js:           # DISCOVER_JS
            return {"sitekey": self.sitekey, "enterprise": self.enterprise,
                    "action": self.action, "widget": self.widget, "ready": self.ready}
        if ".execute(" in js:                                          # mint
            self.mint_js = js
            return {"ok": True, "token": self.token} if (self.mint_ok and self.token) \
                else {"ok": False, "err": "grecaptcha boom"}
        return None

    async def dispatch_mouse_event(self, kind, x, y, **kw):
        if kind == "mouseMoved":
            self.moves.append((x, y))
        elif kind == "mousePressed":
            self.clicks.append((x, y))


def _solve(engine, page, challenge):
    return run(engine.solve(challenge, page, registry=REG, policy=POLICY))


def test_standard_mints_token_and_reports_metadata() -> None:
    page = FakeV3Page(enterprise=False, sitekey="6Lc_std", action="demo_action")
    res = _solve(RecaptchaV3Engine(warm_s=0.0), page, _ch())
    assert res.status is SolveStatus.SOLVED
    assert res.token == "TOK-V3"
    assert res.metadata["enterprise"] is False
    assert res.metadata["sitekey"] == "6Lc_std"
    assert res.metadata["action"] == "demo_action"
    # standard uses grecaptcha.execute, NOT the enterprise namespace.
    assert "grecaptcha.enterprise" not in (page.mint_js or "")


def test_enterprise_path_uses_enterprise_namespace() -> None:
    page = FakeV3Page(enterprise=True, sitekey="6Lc_ent")
    res = _solve(RecaptchaV3Engine(warm_s=0.0), page, _ch())
    assert res.status is SolveStatus.SOLVED
    assert res.metadata["enterprise"] is True
    assert "grecaptcha.enterprise" in (page.mint_js or "")
    assert res.vendor == "recaptcha_enterprise"


def test_mint_failure_is_failed_value() -> None:
    page = FakeV3Page(mint_ok=False)
    res = _solve(RecaptchaV3Engine(warm_s=0.0), page, _ch())
    assert res.status is SolveStatus.FAILED
    assert "mint failed" in res.error


def test_missing_sitekey_fails_with_clear_error() -> None:
    page = FakeV3Page(sitekey=None)
    res = _solve(RecaptchaV3Engine(warm_s=0.0, ready_tries=1), page, _ch())
    assert res.status is SolveStatus.FAILED
    assert "sitekey" in res.error


def test_challenge_overrides_discovered_sitekey_and_action() -> None:
    page = FakeV3Page(sitekey="discovered", action="discovered_action")
    ch = Challenge(family=Family.RECAPTCHA_V3, url="https://2captcha.com/x", host="2captcha.com",
                   sitekey="pinned_key", action="pinned_action")
    res = _solve(RecaptchaV3Engine(warm_s=0.0), page, ch)
    assert res.metadata["sitekey"] == "pinned_key"
    assert res.metadata["action"] == "pinned_action"
    assert res.metadata["action_source"] == "challenge"


def test_action_source_marks_page_vs_guessed_default() -> None:
    # Discovered from a page data-action -> "page".
    page = FakeV3Page(action="demo_action")
    res = _solve(RecaptchaV3Engine(warm_s=0.0), page, _ch())
    assert res.metadata["action"] == "demo_action"
    assert res.metadata["action_source"] == "page"

    # Nothing on the page -> the engine's guessed default, flagged as such so a
    # caller knows a real site's action check may reject it.
    page = FakeV3Page(action=None)
    res = _solve(RecaptchaV3Engine(warm_s=0.0, default_action="verify"), page, _ch())
    assert res.metadata["action"] == "verify"
    assert res.metadata["action_source"] == "default"


def test_warm_seed_is_reproducible_but_default_is_per_solve() -> None:
    # A fixed seed => identical warm trajectory (reproducible for debugging).
    p1, p2 = FakeV3Page(), FakeV3Page()
    _solve(RecaptchaV3Engine(warm_s=0.05, seed=5), p1, _ch())
    _solve(RecaptchaV3Engine(warm_s=0.05, seed=5), p2, _ch())
    assert p1.moves[:10] == p2.moves[:10] and len(p1.moves) > 0
    # Default seed=None draws fresh entropy => trajectory is not the fixed signature.
    assert RecaptchaV3Engine().seed is None


def test_warm_skipped_when_policy_sets_zero() -> None:
    page = FakeV3Page()
    pol = POLICY.merged(models={"recaptcha_v3_warm_s": "0"})
    res = run(RecaptchaV3Engine(warm_s=5.0).solve(_ch(), page, registry=REG, policy=pol))
    assert res.status is SolveStatus.SOLVED
    assert page.moves == []          # no warming happened
    assert res.metadata["warmed_s"] == 0.0


def test_warm_produces_mouse_interaction() -> None:
    page = FakeV3Page()
    _solve(RecaptchaV3Engine(warm_s=0.05), page, _ch())  # tiny warm, still moves
    assert len(page.moves) > 0


# -- wiring ---------------------------------------------------------------

def test_default_solver_registers_v3_and_does_not_route_it() -> None:
    solver = default_solver(POLICY)
    assert isinstance(solver._engines.get(Family.RECAPTCHA_V3), RecaptchaV3Engine)
    assert Family.RECAPTCHA_V3 not in _OUT_OF_SCOPE_ROUTES


def _ch() -> Challenge:
    return Challenge(family=Family.RECAPTCHA_V3, url="https://2captcha.com/demo/recaptcha-v3",
                     host="2captcha.com")
