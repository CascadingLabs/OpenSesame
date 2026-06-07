"""Downloadable OCR model options for local captcha solving."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ModelBackend = Literal["transformers_pipeline", "trocr", "modelscope"]


@dataclass(frozen=True)
class CaptchaOCRModelOption:
    """A downloadable OCR model OpenSesame can run locally."""

    id: str
    repo_id: str
    backend: ModelBackend
    description: str
    license: str
    task: str | None = None
    revision: str | None = None
    trust_remote_code: bool = False
    runnable: bool = True
    recommended: bool = False
    notes: str = ""


@dataclass(frozen=True)
class LocalOCRConfig:
    """Runtime config for local downloadable OCR models."""

    model_id: str = "grafj-conv-transformer-base"
    device: str = "auto"
    cache_dir: Path | None = None
    local_files_only: bool = False
    allow_remote_code: bool = False
    min_confidence: float = 0.0


MODEL_OPTIONS: dict[str, CaptchaOCRModelOption] = {
    "grafj-conv-transformer-base": CaptchaOCRModelOption(
        id="grafj-conv-transformer-base",
        repo_id="Graf-J/captcha-conv-transformer-base",
        backend="transformers_pipeline",
        task="captcha-recognition",
        revision="1896f25517e3e9c2905db37863bc18e774759646",
        trust_remote_code=True,
        license="mit",
        recommended=True,
        description="Small captcha-specific CNN + transformer CTC recognizer.",
        notes="Best first local option for latency/cost; custom HF pipeline.",
    ),
    "grafj-crnn-base": CaptchaOCRModelOption(
        id="grafj-crnn-base",
        repo_id="Graf-J/captcha-crnn-base",
        backend="transformers_pipeline",
        task="captcha-recognition",
        revision="23704b43dbf2a5d314eb2491adebc0436705afdc",
        trust_remote_code=True,
        license="mit",
        description="Small captcha-specific CRNN/CTC recognizer.",
        notes="Useful latency baseline against the conv-transformer.",
    ),
    "anuashok-trocr-v3": CaptchaOCRModelOption(
        id="anuashok-trocr-v3",
        repo_id="anuashok/ocr-captcha-v3",
        backend="trocr",
        license="apache-2.0",
        description="Fine-tuned TrOCR captcha model.",
        notes="Heavier fallback candidate; better when small CTC models miss.",
    ),
    "xiaolv-ocr-captcha": CaptchaOCRModelOption(
        id="xiaolv-ocr-captcha",
        repo_id="xiaolv/ocr-captcha",
        backend="modelscope",
        license="apache-2.0",
        runnable=False,
        description="Captcha OCR checkpoint family with small and big variants.",
        notes="ModelScope-oriented integration; listed for evaluation tracking.",
    ),
}

RUNNABLE_MODEL_OPTIONS: dict[str, CaptchaOCRModelOption] = {
    model_id: option for model_id, option in MODEL_OPTIONS.items() if option.runnable
}


def get_model_option(model_id: str) -> CaptchaOCRModelOption:
    try:
        return MODEL_OPTIONS[model_id]
    except KeyError as exc:
        options = ", ".join(sorted(MODEL_OPTIONS))
        msg = f"unknown model_id {model_id!r}; available: {options}"
        raise ValueError(msg) from exc


def resolve_torch_device(device: str) -> tuple[str, int]:
    """Resolve a user device string to torch and pipeline device values."""

    if device == "cpu":
        return "cpu", -1
    if device.startswith("cuda"):
        index = 0
        if ":" in device:
            index = int(device.split(":", 1)[1])
        return device, index
    if device == "auto":
        try:
            import torch
        except Exception:
            return "cpu", -1
        if torch.cuda.is_available():
            return "cuda:0", 0
        return "cpu", -1
    msg = "device must be 'auto', 'cpu', or a cuda device like 'cuda:0'"
    raise ValueError(msg)
