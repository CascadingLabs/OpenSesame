from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_example() -> ModuleType:
    path = Path(__file__).parents[2] / "examples/cas253_interrupt_handoff.py"
    spec = importlib.util.spec_from_file_location("cas253_interrupt_handoff", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_example_parses_manual_ui_handoff_defaults(monkeypatch) -> None:
    example = _load_example()
    monkeypatch.setattr(sys, "argv", ["cas253_interrupt_handoff.py"])

    args = example.parse_args()

    assert args.kind == "manual"
    assert args.url == "https://example.com"
    assert args.handoff_url is None
    assert args.novnc_url == "http://127.0.0.1:6080"


def test_example_normalizes_browser_appliance_websocket(monkeypatch) -> None:
    example = _load_example()
    monkeypatch.setattr(
        example,
        "json_request",
        lambda _url: {
            "webSocketDebuggerUrl": "ws://container:9222/devtools/browser/demo"
        },
    )

    ws_url = example.resolve_browser_ws_url("http://127.0.0.1:19222/json/version")

    assert ws_url == "ws://127.0.0.1:19222/devtools/browser/demo"
