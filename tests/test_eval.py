from __future__ import annotations

import json
from pathlib import Path

from open_sesame.contracts import CandidateAnswer, SolveResult
from open_sesame.harness.eval import evaluate_corpus, levenshtein, load_jsonl_corpus


def test_load_jsonl_corpus_resolves_relative_images(tmp_path: Path) -> None:
    image = tmp_path / "captcha.jpg"
    image.write_bytes(b"fake")
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        json.dumps({"id": "one", "image": "captcha.jpg", "expected": " A-1 "})
        + "\n"
    )

    samples = load_jsonl_corpus(corpus)

    assert len(samples) == 1
    assert samples[0].id == "one"
    assert samples[0].image == image
    assert samples[0].expected == "A1"


def test_levenshtein_counts_edits() -> None:
    assert levenshtein("W9H5K", "W9H5K") == 0
    assert levenshtein("W9HSK", "W9H5K") == 1
    assert levenshtein("", "ABC") == 3


def test_evaluate_corpus_reports_sequence_accuracy(tmp_path: Path) -> None:
    samples = load_jsonl_corpus(
        _write_corpus(
            tmp_path,
            [
                {"id": "pass", "image": "one.jpg", "expected": "W9H5K"},
                {"id": "fail", "image": "two.jpg", "expected": "ABC12"},
            ],
        )
    )

    def solve_image(path: Path) -> SolveResult:
        answer = "W9H5K" if path.name == "one.jpg" else "ABC1Z"
        return SolveResult(
            kind="answer",
            solver="fake",
            candidates=(
                CandidateAnswer(
                    text=answer,
                    confidence=0.0,
                    source="fake",
                ),
            ),
        )

    summary, results = evaluate_corpus(samples, "fake", solve_image)

    assert summary.total == 2
    assert summary.exact == 1
    assert summary.sequence_accuracy == 0.5
    assert results[0].exact is True
    assert results[1].char_distance == 1


def _write_corpus(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    for row in rows:
        (tmp_path / row["image"]).write_bytes(b"fake")
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return corpus
