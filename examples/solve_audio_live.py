#!/usr/bin/env python3
"""Live reCAPTCHA v2 solve via the audio side-door, through the public API.

This drives **real Google reCAPTCHA** (api2/demo) and mints a real
`g-recaptcha-response` token, fully locally: the audio engine reads the signed
MP3 from the same-origin challenge DOM, a local Whisper model transcribes it,
the answer is typed and verified, and (apply=True) the token lands in the live
page. No paid solver API.

Run (needs the `live` extra + an `ml-audio` extra; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_audio_live.py

The Whisper model is wrapped as a registry Transcriber provider — the seam the
audio engine resolves its model from.
"""

from __future__ import annotations

import asyncio
import sys

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver

DEMO = "https://www.google.com/recaptcha/api2/demo"
WHISPER_MODEL = "openai/whisper-base.en"


class WhisperTranscriber:
    """A :class:`~OpenSesame.api.providers.Transcriber` backed by local Whisper."""

    def __init__(self, model_id: str, device: str) -> None:
        from transformers import pipeline

        self.model_id = model_id
        self.device = device
        self._pipe = pipeline(
            "automatic-speech-recognition",
            model=model_id,
            device=-1,
            model_kwargs={"cache_dir": ".local/hf"},
        )

    def transcribe(self, audio_path: str) -> str:
        out = self._pipe(audio_path)
        return out["text"] if isinstance(out, dict) else str(out)


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=["www.google.com"],
        models={"recaptcha_v2_audio": WHISPER_MODEL, "recaptcha_v2_strategy": "audio"},
    ))
    solver.registry.register_factory(
        "whisper", lambda key: WhisperTranscriber(key.model_id, key.device)
    )

    async with BrowserSession(BrowserConfig(headless=True, stealth=True,
                                            extra_args=["--window-size=1365,900"])) as browser:
        page = await browser.new_page("about:blank")
        await page.goto(DEMO, timeout=40)

        async with solver.engine():       # warm Whisper once
            # VoidCrawl would normally describe the challenge; on the demo we
            # know it's reCAPTCHA v2, so build the descriptor directly.
            challenge = Challenge.from_capture({"kind": "recaptcha", "page_url": DEMO})
            result = await solver.solve(challenge, page=page, timeout=120)

        if result.ok and result.solution.is_token:
            print(f"✓ PASSED — minted a real reCAPTCHA token "
                  f"({len(result.token)} chars, transcript={result.metadata.get('transcript')!r}, "
                  f"applied={result.applied}, {result.timing.elapsed_ms:.0f}ms)")
            print(f"  token preview: {result.token[:48]}...")
            return 0

        print(f"✗ not solved: status={result.status.value} error={result.error!r}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
