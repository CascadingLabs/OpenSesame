"""Local ML OCR solver for downloadable captcha recognition models."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from open_sesame.contracts import CandidateAnswer, SolveResult
from open_sesame.solvers.ml_config import (
    LocalOCRConfig,
    get_model_option,
    resolve_torch_device,
)
from open_sesame.solvers.ocr import normalize_ocr_text


class LocalMLCaptchaOCRSolver:
    """Run a configured local ML captcha OCR model."""

    solver_name = "local-ml-captcha-ocr"

    def __init__(self, config: LocalOCRConfig | None = None) -> None:
        self.config = config or LocalOCRConfig()
        self.option = get_model_option(self.config.model_id)
        if not self.option.runnable:
            msg = f"model {self.option.id!r} is listed for research but is not runnable"
            raise NotImplementedError(msg)
        if self.option.trust_remote_code and not self.config.allow_remote_code:
            msg = (
                f"model {self.option.id!r} requires trusted remote code; "
                "set allow_remote_code=True after reviewing/pinning the model"
            )
            raise RuntimeError(msg)
        self._loaded: Any | None = None

    def download(self) -> Path:
        """Download the configured model snapshot and return its local path."""

        self._prepare_cache_env()
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            msg = "Install the 'ml' extra to download local OCR models."
            raise RuntimeError(msg) from exc

        return Path(
            snapshot_download(
                repo_id=self.option.repo_id,
                revision=self.option.revision,
                cache_dir=str(self.config.cache_dir) if self.config.cache_dir else None,
                local_files_only=self.config.local_files_only,
            )
        )

    def load(self) -> None:
        """Load the configured OCR model into memory."""

        if self.option.backend == "transformers_pipeline":
            self._load_transformers_pipeline()
            return
        if self.option.backend == "trocr":
            self._load_trocr()
            return
        msg = f"backend {self.option.backend!r} is listed but not implemented locally"
        raise NotImplementedError(msg)

    def solve_image(self, image_path: str | Path) -> SolveResult:
        path = Path(image_path)
        raw = self._predict(path)
        text, confidence = _extract_prediction(raw)
        normalized = normalize_ocr_text(text)

        candidates: tuple[CandidateAnswer, ...]
        if normalized and confidence >= self.config.min_confidence:
            candidates = (
                CandidateAnswer(
                    text=normalized,
                    confidence=confidence,
                    source=f"{self.solver_name}:{self.option.id}",
                    raw_text=text,
                    metadata={
                        "image_path": str(path),
                        "model_id": self.option.id,
                        "repo_id": self.option.repo_id,
                        "raw_prediction": raw,
                    },
                ),
            )
        else:
            candidates = ()

        torch_device, _ = resolve_torch_device(self.config.device)
        return SolveResult(
            kind="answer",
            solver=self.solver_name,
            candidates=candidates,
            metadata={
                "model_id": self.option.id,
                "repo_id": self.option.repo_id,
                "backend": self.option.backend,
                "device": torch_device,
                "raw_prediction": raw,
            },
        )

    def _predict(self, image_path: Path) -> Any:
        try:
            from PIL import Image
        except ImportError as exc:
            msg = "Install the 'ml' extra to run local OCR models."
            raise RuntimeError(msg) from exc

        if self.option.backend == "transformers_pipeline":
            pipe = self._load_transformers_pipeline()
            image = Image.open(image_path).convert("RGB")
            return pipe(image)
        if self.option.backend == "trocr":
            return self._predict_trocr(image_path)
        msg = f"backend {self.option.backend!r} is listed but not implemented locally"
        raise NotImplementedError(msg)

    def _load_transformers_pipeline(self) -> Any:
        if self._loaded is not None:
            return self._loaded
        self._prepare_cache_env()
        try:
            from transformers import pipeline
        except ImportError as exc:
            msg = "Install the 'ml' extra to run local OCR models."
            raise RuntimeError(msg) from exc

        _, pipeline_device = resolve_torch_device(self.config.device)
        model_ref = self._model_ref()
        self._loaded = pipeline(
            task=self.option.task,
            model=model_ref,
            trust_remote_code=self.config.allow_remote_code,
            device=pipeline_device,
        )
        return self._loaded

    def _predict_trocr(self, image_path: Path) -> dict[str, Any]:
        self._load_trocr()
        processor, model, torch_device, torch = self._loaded
        try:
            from PIL import Image
        except ImportError as exc:
            msg = "Install the 'ml' extra to run local OCR models."
            raise RuntimeError(msg) from exc

        image = Image.open(image_path).convert("RGB")
        pixel_values = processor(image, return_tensors="pt").pixel_values.to(torch_device)
        with torch.no_grad():
            generated_ids = model.generate(pixel_values)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return {"generated_text": text}

    def _load_trocr(self) -> None:
        if self._loaded is not None:
            return
        self._prepare_cache_env()
        try:
            import torch
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        except ImportError as exc:
            msg = "Install the 'ml' extra to run the TrOCR captcha model."
            raise RuntimeError(msg) from exc

        torch_device, _ = resolve_torch_device(self.config.device)
        model_ref = self._model_ref()
        processor = TrOCRProcessor.from_pretrained(model_ref)
        model = VisionEncoderDecoderModel.from_pretrained(model_ref).to(torch_device)
        model.eval()
        self._loaded = (processor, model, torch_device, torch)

    def _model_ref(self) -> str:
        if not self.config.cache_dir and not self.config.local_files_only:
            return self.option.repo_id
        return str(self.download())

    def _prepare_cache_env(self) -> None:
        if self.config.cache_dir is None:
            return
        cache_dir = Path(self.config.cache_dir)
        modules_dir = cache_dir / "modules"
        cache_dir.mkdir(parents=True, exist_ok=True)
        modules_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(cache_dir))
        os.environ.setdefault("HF_HUB_CACHE", str(cache_dir / "hub"))
        os.environ.setdefault("HF_MODULES_CACHE", str(modules_dir))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_dir / "transformers"))


def _extract_prediction(raw: Any) -> tuple[str, float]:
    """Normalize common HF output shapes to text and confidence."""

    if isinstance(raw, list) and raw:
        return _extract_prediction(raw[0])
    if isinstance(raw, dict):
        for key in ("prediction", "generated_text", "text", "label"):
            value = raw.get(key)
            if value is not None:
                confidence = raw.get("score", raw.get("confidence", 0.0))
                return str(value), _coerce_confidence(confidence)
    if isinstance(raw, str):
        return raw, 0.0
    return str(raw), 0.0


def _coerce_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))
