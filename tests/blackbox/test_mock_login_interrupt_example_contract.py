from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from urllib import error, parse, request


def _load_example() -> ModuleType:
    path = Path(__file__).parents[2] / "examples/mock_login_interrupt_handoff.py"
    spec = importlib.util.spec_from_file_location("mock_login_interrupt_handoff", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def test_mock_login_example_serves_disposable_http_only_session() -> None:
    example = _load_example()
    body = parse.urlencode(
        {"username": example.MOCK_USERNAME, "password": example.MOCK_PASSWORD}
    ).encode()
    opener = request.build_opener(NoRedirect)

    with example.mock_login_server() as base_url:
        login = request.Request(f"{base_url}/login", data=body, method="POST")
        try:
            opener.open(login)
        except error.HTTPError as response:
            assert response.code == 303
            assert response.headers["Location"] == "/secure"
            cookie = response.headers["Set-Cookie"]
        else:  # pragma: no cover - NoRedirect must preserve the 303 response
            raise AssertionError("login unexpectedly followed its redirect")

        assert "mock_session=approved" in cookie
        assert "HttpOnly" in cookie
        secure = request.Request(
            f"{base_url}/secure", headers={"Cookie": cookie.split(";", 1)[0]}
        )
        with request.urlopen(secure) as response:
            body = response.read().decode()

    assert "Mock Secure Area" in body


def test_mock_login_example_defaults_to_novnc(monkeypatch) -> None:
    example = _load_example()
    monkeypatch.setattr(sys, "argv", ["mock_login_interrupt_handoff.py"])

    args = example.parse_args()

    assert args.novnc_url == "http://127.0.0.1:6080"
    assert args.timeout == 600
