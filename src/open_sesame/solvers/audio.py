"""Local audio transcription helpers for captcha challenge research."""

from __future__ import annotations

from typing import Any


def extract_asr_text(raw: Any) -> str:
    """Extract generated text from common ASR pipeline outputs."""

    if isinstance(raw, str):
        return normalize_asr_text(raw)
    if isinstance(raw, dict):
        text = raw.get("text")
        if text is not None:
            return normalize_asr_text(str(text))
    if isinstance(raw, list) and raw:
        return extract_asr_text(raw[0])
    return ""


def normalize_asr_text(text: str) -> str:
    """Normalize speech-to-text output without removing meaningful words."""

    return " ".join(text.strip().split())
