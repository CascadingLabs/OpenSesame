from __future__ import annotations

from open_sesame.solvers.audio import extract_asr_text, normalize_asr_text


def test_normalize_asr_text_collapses_whitespace() -> None:
    assert normalize_asr_text("  one   two\nthree  ") == "one two three"


def test_extract_asr_text_handles_pipeline_shapes() -> None:
    assert extract_asr_text({"text": "  four five  "}) == "four five"
    assert extract_asr_text([{"text": "six"}]) == "six"
    assert extract_asr_text("seven") == "seven"
    assert extract_asr_text({}) == ""
