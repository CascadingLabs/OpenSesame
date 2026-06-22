"""Model-inference seams the engines depend on.

Engines own *orchestration* (read the live DOM, drive it, assemble a result) but
delegate *inference* to a provider resolved from the model registry. The heavy
implementations (Whisper, ViT/CLIP, OCR) live in the solver modules and register
factories for these kinds; the API package depends only on these Protocols, so
it stays import-light and is tested with fakes.

Registry ``kind`` constants used to resolve a provider:
    "whisper" -> Transcriber, "tiles" -> TileSelector, "ocr" -> TextReader,
    "vlm" -> VisionReasoner
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Transcriber(Protocol):
    """ASR for the reCAPTCHA audio side-door / standalone audio captchas."""

    model_id: str
    device: str

    def transcribe(self, audio_path: str) -> str: ...


@runtime_checkable
class TileSelector(Protocol):
    """Pick the target tiles of a reCAPTCHA image grid.

    Returns ``(row, col, score)`` for each tile judged to contain the target.
    """

    model_id: str
    device: str

    def select_tiles(
        self,
        image_path: str,
        *,
        rows: int,
        cols: int,
        target: str,
        candidate_labels: tuple[str, ...] = (),
    ) -> list[tuple[int, int, float]]: ...


@runtime_checkable
class TextReader(Protocol):
    """OCR for distorted-text captchas. Returns (text, confidence)."""

    model_id: str
    device: str

    def read_text(self, image_path: str) -> tuple[str, float]: ...


@runtime_checkable
class VisionReasoner(Protocol):
    """Grounds a click point for a canvas captcha (hCaptcha).

    hCaptcha's challenges are *animated* — the cards reveal their content one at a
    time, so no single frame shows every cell. ``locate_burst`` (the engine-facing
    call) collapses a temporal burst of canvas screenshots into one image and
    grounds a point on it; a non-animated challenge is just a burst of length 1.
    """

    model_id: str
    device: str

    def locate_burst(
        self, frame_paths: list[str], *, instruction: str
    ) -> tuple[float, float, float]:
        """Collapse a temporal burst and return (x, y, confidence) in [0,1]."""
