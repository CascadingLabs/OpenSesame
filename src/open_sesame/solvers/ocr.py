"""OCR fast-path for normal distorted text captchas."""

from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from open_sesame.contracts import CandidateAnswer, SolveResult

DEFAULT_WHITELIST = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def normalize_ocr_text(text: str) -> str:
    """Normalize OCR output into a captcha answer candidate."""

    return _ALNUM.sub("", text).strip()


@dataclass(frozen=True)
class TesseractConfig:
    executable: str = "tesseract"
    lang: str = "eng"
    psm: int = 8
    whitelist: str = DEFAULT_WHITELIST
    min_confidence: float = 0.0


CompletedProcessRunner = Callable[
    [Sequence[str]], subprocess.CompletedProcess[str]
]


class TesseractOCRSolver:
    """Run Tesseract in TSV mode and convert output into a direct answer."""

    solver_name = "tesseract-ocr"

    def __init__(
        self,
        config: TesseractConfig | None = None,
        runner: CompletedProcessRunner | None = None,
    ) -> None:
        self.config = config or TesseractConfig()
        self._runner = runner or self._run_command

    def solve_image(self, image_path: str | Path) -> SolveResult:
        path = Path(image_path)
        command = self._command(path)
        completed = self._runner(command)
        raw_text, confidence = parse_tesseract_tsv(completed.stdout)
        normalized = normalize_ocr_text(raw_text)

        candidates: tuple[CandidateAnswer, ...]
        if normalized and confidence >= self.config.min_confidence:
            candidates = (
                CandidateAnswer(
                    text=normalized,
                    confidence=confidence,
                    source=self.solver_name,
                    raw_text=raw_text,
                    metadata={"image_path": str(path), "psm": self.config.psm},
                ),
            )
        else:
            candidates = ()

        return SolveResult(
            kind="answer",
            solver=self.solver_name,
            candidates=candidates,
            metadata={
                "command": command,
                "raw_text": raw_text,
                "confidence": confidence,
            },
        )

    def _command(self, image_path: Path) -> list[str]:
        return [
            self.config.executable,
            str(image_path),
            "stdout",
            "--psm",
            str(self.config.psm),
            "-l",
            self.config.lang,
            "-c",
            f"tessedit_char_whitelist={self.config.whitelist}",
            "tsv",
        ]

    @staticmethod
    def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
        )


def parse_tesseract_tsv(tsv: str) -> tuple[str, float]:
    """Return joined text and mean confidence from Tesseract TSV output."""

    reader = csv.DictReader(tsv.splitlines(), delimiter="\t")
    words: list[str] = []
    confidences: list[float] = []

    for row in reader:
        text = (row.get("text") or "").strip()
        confidence_text = (row.get("conf") or "").strip()
        if not text:
            continue

        words.append(text)
        try:
            confidence = float(confidence_text)
        except ValueError:
            continue
        if confidence >= 0:
            confidences.append(confidence / 100.0)

    if not words:
        return "", 0.0

    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return "".join(words), confidence
