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


@dataclass(frozen=True)
class TorchDeviceInfo:
    """Resolved torch device details for local ML workloads."""

    torch_device: str
    pipeline_device: int
    accelerator: str
    torch_version: str | None = None
    hip_version: str | None = None
    cuda_version: str | None = None
    device_name: str | None = None

    @property
    def uses_gpu(self) -> bool:
        return self.pipeline_device >= 0

    @property
    def is_rocm(self) -> bool:
        return self.accelerator == "rocm"

    def as_dict(self) -> dict[str, object]:
        return {
            "torch_device": self.torch_device,
            "pipeline_device": self.pipeline_device,
            "accelerator": self.accelerator,
            "torch_version": self.torch_version,
            "hip_version": self.hip_version,
            "cuda_version": self.cuda_version,
            "device_name": self.device_name,
        }


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


def resolve_torch_device_info(device: str) -> TorchDeviceInfo:
    """Resolve a user device string to torch and HF pipeline device values.

    ROCm PyTorch exposes AMD GPUs through the ``torch.cuda`` API. That means a
    ROCm install should still resolve to ``cuda:0`` for model placement, with
    ``accelerator='rocm'`` recorded for diagnostics.
    """

    if device == "cpu":
        return TorchDeviceInfo(torch_device="cpu", pipeline_device=-1, accelerator="cpu")
    if device.startswith("cuda"):
        index = 0
        if ":" in device:
            index = int(device.split(":", 1)[1])
        torch = _import_torch()
        return TorchDeviceInfo(
            torch_device=device,
            pipeline_device=index,
            accelerator=_torch_accelerator(torch),
            torch_version=_torch_attr(torch, "__version__"),
            hip_version=_torch_version_attr(torch, "hip"),
            cuda_version=_torch_version_attr(torch, "cuda"),
            device_name=_torch_device_name(torch, index),
        )
    if device == "auto":
        torch = _import_torch()
        if torch is None:
            return TorchDeviceInfo(torch_device="cpu", pipeline_device=-1, accelerator="cpu")
        if torch.cuda.is_available():
            return TorchDeviceInfo(
                torch_device="cuda:0",
                pipeline_device=0,
                accelerator=_torch_accelerator(torch),
                torch_version=_torch_attr(torch, "__version__"),
                hip_version=_torch_version_attr(torch, "hip"),
                cuda_version=_torch_version_attr(torch, "cuda"),
                device_name=_torch_device_name(torch, 0),
            )
        return TorchDeviceInfo(
            torch_device="cpu",
            pipeline_device=-1,
            accelerator="cpu",
            torch_version=_torch_attr(torch, "__version__"),
            hip_version=_torch_version_attr(torch, "hip"),
            cuda_version=_torch_version_attr(torch, "cuda"),
        )
    msg = "device must be 'auto', 'cpu', or a cuda device like 'cuda:0'"
    raise ValueError(msg)


def resolve_torch_device(device: str) -> tuple[str, int]:
    """Resolve a user device string to torch and pipeline device values."""

    info = resolve_torch_device_info(device)
    return info.torch_device, info.pipeline_device


def _import_torch() -> object | None:
    try:
        import torch
    except Exception:
        return None
    return torch


def _torch_accelerator(torch: object | None) -> str:
    if torch is None:
        return "cuda"
    if _torch_version_attr(torch, "hip"):
        return "rocm"
    if _torch_version_attr(torch, "cuda"):
        return "cuda"
    return "cuda"


def _torch_attr(torch: object | None, name: str) -> str | None:
    if torch is None:
        return None
    value = getattr(torch, name, None)
    return str(value) if value is not None else None


def _torch_version_attr(torch: object | None, name: str) -> str | None:
    version = getattr(torch, "version", None) if torch is not None else None
    value = getattr(version, name, None)
    return str(value) if value else None


def _torch_device_name(torch: object | None, index: int) -> str | None:
    if torch is None:
        return None
    cuda = getattr(torch, "cuda", None)
    get_device_name = getattr(cuda, "get_device_name", None)
    if get_device_name is None:
        return None
    try:
        return str(get_device_name(index))
    except Exception:
        return None
