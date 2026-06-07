from __future__ import annotations

from open_sesame.harness.process import (
    parse_float_key_value_output,
    parse_key_value_output,
)


def test_parse_key_value_output_returns_answer_line() -> None:
    output = "warning\nanswer=W9H5K\nconfidence=0.000\n"

    assert parse_key_value_output(output, "answer") == "W9H5K"


def test_parse_float_key_value_output_clamps_confidence() -> None:
    assert parse_float_key_value_output("confidence=2.0\n", "confidence") == 1.0
    assert parse_float_key_value_output("confidence=bad\n", "confidence") == 0.0
