"""Unit tests for MTCaptcha pure helpers (no live browser)."""

from __future__ import annotations

import base64

from open_sesame.harness.mtcaptcha import (
    MtRect,
    decode_data_url_png,
    parse_mt_state,
    plausible_answer,
)


def test_parse_mt_state_reads_token_and_iframe() -> None:
    raw = {
        "sitekey": "MTPublic-KzqLY1cKH",
        "token": "v1(abc...)",
        "has_api": "object",
        "iframe": {"x": 667.0, "y": 282.0, "width": 300.0, "height": 176.0},
    }
    state = parse_mt_state(raw)
    assert state.sitekey == "MTPublic-KzqLY1cKH"
    assert state.solved is True
    assert state.has_api is True
    assert state.iframe == MtRect(667.0, 282.0, 300.0, 176.0)


def test_parse_mt_state_unsolved_when_token_empty() -> None:
    state = parse_mt_state({"sitekey": "k", "token": "", "has_api": "object", "iframe": None})
    assert state.solved is False
    assert state.iframe is None


def test_decode_data_url_png_roundtrip() -> None:
    payload = b"\x89PNG\r\n\x1a\n-not-a-real-png-but-bytes"
    data_url = "data:image/png;base64," + base64.b64encode(payload).decode()
    assert decode_data_url_png(data_url) == payload


def test_decode_data_url_png_rejects_non_data_url() -> None:
    assert decode_data_url_png("https://example.com/x.png") is None
    assert decode_data_url_png("ERR:tainted canvas") is None


def test_plausible_answer_bounds() -> None:
    assert plausible_answer("aB3xZ") is True
    assert plausible_answer("ab") is False
    assert plausible_answer("") is False
    assert plausible_answer("waytoolonganswerstring") is False


def test_decode_data_url_png_handles_gif_payload() -> None:
    # MTCaptcha serves the word as a GIF data URL; the decoder is format-agnostic.
    payload = b"GIF89a\x00\x00fake-gif-bytes"
    data_url = "data:image/gif;base64," + base64.b64encode(payload).decode()
    assert decode_data_url_png(data_url) == payload
