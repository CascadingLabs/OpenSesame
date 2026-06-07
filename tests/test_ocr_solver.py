from __future__ import annotations

import subprocess

from open_sesame.solvers.ocr import (
    TesseractConfig,
    TesseractOCRSolver,
    normalize_ocr_text,
    parse_tesseract_tsv,
)


TESSERACT_TSV = "\n".join(
    [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t92.5\tA8",
        "5\t1\t1\t1\t1\t2\t10\t0\t10\t10\t87.5\tb-2",
    ]
)


def test_normalize_ocr_text_keeps_only_answer_characters() -> None:
    assert normalize_ocr_text(" A8 b-2\n") == "A8b2"


def test_parse_tesseract_tsv_joins_text_and_averages_confidence() -> None:
    text, confidence = parse_tesseract_tsv(TESSERACT_TSV)

    assert text == "A8b-2"
    assert confidence == 0.9


def test_tesseract_solver_returns_candidate_from_tsv() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=TESSERACT_TSV, stderr="")

    solver = TesseractOCRSolver(
        TesseractConfig(executable="fake-tesseract", min_confidence=0.5),
        runner=runner,
    )

    result = solver.solve_image("captcha.png")

    assert result.kind == "answer"
    assert result.best is not None
    assert result.best.text == "A8b2"
    assert result.best.confidence == 0.9
    assert result.best.source == "tesseract-ocr"
    assert result.metadata["command"][0] == "fake-tesseract"


def test_tesseract_solver_drops_low_confidence_candidate() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=TESSERACT_TSV, stderr="")

    solver = TesseractOCRSolver(
        TesseractConfig(min_confidence=0.95),
        runner=runner,
    )

    result = solver.solve_image("captcha.png")

    assert result.best is None
    assert result.candidates == ()
