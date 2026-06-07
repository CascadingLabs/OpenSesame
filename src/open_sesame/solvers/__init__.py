"""Captcha solver implementations."""

from open_sesame.solvers.local_ml import LocalMLCaptchaOCRSolver
from open_sesame.solvers.ml_config import LocalOCRConfig, MODEL_OPTIONS, RUNNABLE_MODEL_OPTIONS
from open_sesame.solvers.ocr import TesseractOCRSolver, normalize_ocr_text

__all__ = [
    "LocalMLCaptchaOCRSolver",
    "LocalOCRConfig",
    "MODEL_OPTIONS",
    "RUNNABLE_MODEL_OPTIONS",
    "TesseractOCRSolver",
    "normalize_ocr_text",
]
