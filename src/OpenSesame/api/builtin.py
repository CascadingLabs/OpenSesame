"""Built-in local-model providers; the opinionated batteries.

OpenSesame owns the captcha-specific glue (tile splitting, reCAPTCHA label
normalization, ASR/OCR wrapping) so a caller never writes it. These providers
wrap the common local models behind the registry seams and are registered
automatically by ``install_default_providers`` when their ML extra is installed.
Imports are lazy so the API package stays import-light on an API-only checkout.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

# reCAPTCHA prompt phrasings -> the model's label vocabulary.
_LABEL_ALIASES = {
    "fire hydrant": "hydrant",
    "fire hydrants": "hydrant",
    "traffic lights": "traffic light",
    "palm trees": "palm",
    "palm tree": "palm",
}


def _cache_kwargs(cache_dir: str | None) -> dict[str, Any]:
    return {"cache_dir": cache_dir} if cache_dir else {}


def normalize_target_labels(target: str) -> set[str]:
    """Expand a reCAPTCHA target ('buses', 'fire hydrants') to model labels."""

    t = " ".join(target.strip().lower().split())
    cands = {t}
    if t in _LABEL_ALIASES:
        cands.add(_LABEL_ALIASES[t])
    if t.endswith("es"):
        cands.add(t[:-2])
    if t.endswith("s"):
        cands.add(t[:-1])
    return cands


class WhisperTranscriber:
    """Local Whisper ASR (reCAPTCHA audio side-door / standalone audio)."""

    def __init__(self, model_id: str, device: str, *, cache_dir: str | None = None) -> None:
        from transformers import pipeline

        self.model_id = model_id
        self.device = device
        self._pipe = pipeline(
            "automatic-speech-recognition", model=model_id, device=-1,
            model_kwargs=_cache_kwargs(cache_dir),  # else HF_HOME / default cache
        )

    def transcribe(self, audio_path: str) -> str:
        out = self._pipe(audio_path)
        return out["text"] if isinstance(out, dict) else str(out)


class ViTTileSelector:
    """Local image classifier over reCAPTCHA grid tiles.

    OpenSesame splits the grid, classifies each tile with the model, normalizes
    the prompt target to the model's labels, and returns the matching cells;
    the caller just names the model.
    """

    def __init__(self, model_id: str, device: str, *, cache_dir: str | None = None,
                 min_score: float = 0.5) -> None:
        from transformers import pipeline

        self.model_id = model_id
        self.device = device
        self.min_score = min_score
        self._pipe = pipeline(
            "image-classification", model=model_id, device=-1,
            model_kwargs=_cache_kwargs(cache_dir),  # else HF_HOME / default cache
        )
        self._tmp = Path(tempfile.mkdtemp(prefix="os-tiles-"))

    def select_tiles(self, image_path, *, rows, cols, target, candidate_labels=()):
        from PIL import Image

        labels = normalize_target_labels(target)
        img = Image.open(image_path).convert("RGB")
        tw, th = img.width // cols, img.height // rows
        picks = []
        for r in range(rows):
            for c in range(cols):
                tile = img.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th))
                tile_path = self._tmp / "tile.png"
                tile.save(tile_path)
                top = self._pipe(str(tile_path), top_k=1)[0]
                label, score = str(top["label"]).lower(), float(top["score"])
                if score >= self.min_score and (label in labels or label.rstrip("s") in labels):
                    picks.append((r, c, score))
        return picks


class CaptchaPipelineTextReader:
    """A captcha-trained HF OCR model (e.g. ``Graf-J/captcha-conv-transformer-base``).

    These ship a custom ``captcha-recognition`` pipeline + processor via
    ``trust_remote_code``; OpenSesame wires the processor (newer transformers
    don't auto-attach it) so the caller just names the model.
    """

    def __init__(self, model_id: str, device: str) -> None:
        from transformers import AutoProcessor, pipeline

        # Allow "repo@revision" to pin a reproducible (and offline-cacheable) load.
        repo, _, revision = model_id.partition("@")
        self.model_id = model_id
        self.device = device
        self._pipe = pipeline(
            task="captcha-recognition", model=repo, revision=revision or None,
            trust_remote_code=True, device=-1,
        )
        if getattr(self._pipe, "processor", None) is None:
            self._pipe.processor = AutoProcessor.from_pretrained(
                repo, revision=revision or None, trust_remote_code=True
            )

    def read_text(self, image_path: str) -> tuple[str, float]:
        from PIL import Image

        out = self._pipe(Image.open(image_path).convert("RGB"))
        text = ""
        if isinstance(out, dict):
            text = out.get("prediction") or out.get("generated_text") or out.get("text") or ""
        text = "".join(str(text).split())
        return text, 1.0 if text else 0.0


class TesseractTextReader:
    """Tesseract OCR for distorted-text captchas."""

    model_id = "tesseract"

    def __init__(self, model_id: str = "tesseract", device: str = "cpu", *,
                 whitelist: str = "") -> None:
        self.model_id = model_id
        self.device = device
        self.whitelist = whitelist

    def read_text(self, image_path: str) -> tuple[str, float]:
        cmd = ["tesseract", image_path, "stdout", "--psm", "8"]
        if self.whitelist:
            cmd += ["-c", f"tessedit_char_whitelist={self.whitelist}"]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        text = "".join(out.split())
        return text, 1.0 if text else 0.0


def register_builtin_providers(registry: Any, *, cache_dir: str = ".local/hf") -> None:
    """Register the built-in factories that have their dependencies installed."""

    if not registry.has_factory("whisper"):
        try:
            import transformers  # noqa: F401

            registry.register_factory(
                "whisper", lambda key: WhisperTranscriber(key.model_id, key.device, cache_dir=cache_dir)
            )
        except Exception:
            pass
    if not registry.has_factory("tiles"):
        try:
            import transformers  # noqa: F401

            registry.register_factory(
                "tiles", lambda key: ViTTileSelector(key.model_id, key.device, cache_dir=cache_dir)
            )
        except Exception:
            pass
    if not registry.has_factory("ocr"):
        registry.register_factory("ocr", _build_ocr_reader)


def _build_ocr_reader(key: Any) -> Any:
    """Route an OCR model id to the right reader: HF captcha model vs Tesseract."""

    model_id = key.model_id or "tesseract"
    if "/" in model_id:                       # a Hugging Face captcha-OCR model
        return CaptchaPipelineTextReader(model_id, key.device)
    from shutil import which

    if not which("tesseract"):
        raise LookupError("tesseract not on PATH and model_id is not an HF repo")
    return TesseractTextReader(model_id, key.device)
