"""Unit tests for the OpenSesame solver-on-tap MCP tools (no browser).

Patches the CDP attach (``_open``) and the ``Solver`` so the tool wiring —
family routing, allow-list scoping to the adopted host, error-as-value, and
result serialization — is verified without a live Chrome.
"""

from __future__ import annotations

import asyncio

from OpenSesame.api.result import (
    AnswerSolution,
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    Timing,
)
from OpenSesame.mcp import server as mcp


class _FakePage:
    def __init__(self, url="https://demo.example.com/captcha", kind=None, capture=None):
        self._url, self._kind, self._capture = url, kind, capture

    async def url(self):
        return self._url

    async def detect_captcha(self):
        return self._kind

    async def capture_captcha(self):
        return self._capture


class _FakePageDetectOnly:
    """A page exposing detect_captcha but NOT capture_captcha (older browser build)."""

    def __init__(self, url="https://site.test/p", kind="turnstile"):
        self._url, self._kind = url, kind

    async def url(self):
        return self._url

    async def detect_captcha(self):
        return self._kind

    # intentionally no `capture_captcha` attribute


class _FakeSession:
    def __init__(self, page):
        self._page = page

    async def attach_page(self, target_id):
        return self._page

    async def __aexit__(self, *a):
        return False


def _install(monkeypatch, page, result=None):
    """Patch `_open` + `_get_solver`; return a dict capturing what the tool did."""
    captured: dict = {}

    async def fake_open(ws_url):
        captured["ws_url"] = ws_url
        return _FakeSession(page)

    class _FakeSolver:
        async def solve(self, challenge, page, *, timeout=None, **overrides):
            captured["challenge"] = challenge
            captured["overrides"] = overrides
            captured["timeout"] = timeout
            return result

    monkeypatch.setattr(mcp, "_open", fake_open)
    monkeypatch.setattr(mcp, "_get_solver", lambda: _FakeSolver())
    return captured


def test_solve_unknown_family_is_value(monkeypatch):
    _install(monkeypatch, _FakePage())
    out = asyncio.run(mcp.solve("ws://x", "TID", family="nope"))
    assert out["ok"] is False
    assert "unknown family" in out["error"]


def test_solve_named_family_routes_and_scopes_host(monkeypatch):
    result = SolveResult(
        status=SolveStatus.SOLVED,
        family=Family.ROTATE,
        solution=AnswerSolution("255"),
        solved_by=SolvedBy.LOCAL,
        timing=Timing(0.0, 123.0),
        metadata={"strategy": "rotate-verify"},
    )
    cap = _install(
        monkeypatch, _FakePage(url="https://2captcha.com/demo/rotatecaptcha"), result
    )
    out = asyncio.run(mcp.solve("ws://x", "TID", family="rotate"))

    # routed to the rotate engine, with the adopted host auto-allowed (default-deny)
    assert cap["challenge"].family is Family.ROTATE
    assert cap["overrides"]["allow_sites"] == ["2captcha.com"]
    assert cap["overrides"]["apply"] is True
    # serialized faithfully
    assert out == {
        "ok": True,
        "status": "solved",
        "family": "rotate",
        "token": None,
        "answer": "255",
        "applied": False,
        "confidence": None,
        "solved_by": "local",
        "error": "",
        "metadata": {"strategy": "rotate-verify"},
        "elapsed_ms": 123.0,
    }


def test_solve_recaptcha_alias(monkeypatch):
    cap = _install(
        monkeypatch,
        _FakePage(url="https://site.test/p"),
        SolveResult(status=SolveStatus.FAILED, family=Family.RECAPTCHA_V2, timing=Timing(0.0, 1.0)),
    )
    asyncio.run(mcp.solve("ws://x", "TID", family="recaptcha"))
    assert cap["challenge"].family is Family.RECAPTCHA_V2
    assert cap["overrides"]["allow_sites"] == ["site.test"]


def test_solve_autodetect_no_captcha_is_value(monkeypatch):
    _install(monkeypatch, _FakePage(capture=None))
    out = asyncio.run(mcp.solve("ws://x", "TID"))  # family=None, nothing on the tab
    assert out["ok"] is False
    assert "no captcha" in out["error"]


def test_solve_autodetect_falls_back_to_detect(monkeypatch):
    # Browser build without capture_captcha: auto-detect must still work via the
    # lighter detect_captcha probe (kind only) and route to the right family.
    cap = _install(
        monkeypatch,
        _FakePageDetectOnly(kind="turnstile"),
        SolveResult(status=SolveStatus.SOLVED, family=Family.TURNSTILE, timing=Timing(0.0, 1.0)),
    )
    out = asyncio.run(mcp.solve("ws://x", "TID"))  # family=None, no capture_captcha
    assert cap["challenge"].family is Family.TURNSTILE
    assert cap["overrides"]["allow_sites"] == ["site.test"]
    assert out["ok"] is True


def test_detect_returns_kind(monkeypatch):
    _install(monkeypatch, _FakePage(kind="turnstile"))
    out = asyncio.run(mcp.detect("ws://x", "TID"))
    assert out == {"kind": "turnstile"}
