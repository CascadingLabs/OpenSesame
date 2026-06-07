"""OCR corpus evaluation helpers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from open_sesame.contracts import SolveResult
from open_sesame.solvers.ocr import normalize_ocr_text


@dataclass(frozen=True)
class CorpusSample:
    id: str
    image: Path
    expected: str
    source: str = ""


@dataclass(frozen=True)
class EvalSampleResult:
    id: str
    image: str
    expected: str
    answer: str
    exact: bool
    char_distance: int
    cer: float
    elapsed_ms: float
    solver: str
    source: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "image": self.image,
            "expected": self.expected,
            "answer": self.answer,
            "exact": self.exact,
            "char_distance": self.char_distance,
            "cer": self.cer,
            "elapsed_ms": self.elapsed_ms,
            "solver": self.solver,
            "source": self.source,
        }


@dataclass(frozen=True)
class EvalSummary:
    total: int
    exact: int
    sequence_accuracy: float
    mean_cer: float
    latency_ms_avg: float
    latency_ms_p95: float

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "exact": self.exact,
            "sequence_accuracy": self.sequence_accuracy,
            "mean_cer": self.mean_cer,
            "latency_ms_avg": self.latency_ms_avg,
            "latency_ms_p95": self.latency_ms_p95,
        }


def load_jsonl_corpus(path: str | Path) -> tuple[CorpusSample, ...]:
    corpus_path = Path(path)
    samples: list[CorpusSample] = []
    for line_number, line in enumerate(corpus_path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        try:
            image = Path(row["image"])
            expected = str(row["expected"])
        except KeyError as exc:
            msg = f"{corpus_path}:{line_number} missing required field {exc.args[0]!r}"
            raise ValueError(msg) from exc
        if not image.is_absolute():
            image = corpus_path.parent / image
        samples.append(
            CorpusSample(
                id=str(row.get("id") or f"sample-{line_number}"),
                image=image,
                expected=normalize_ocr_text(expected),
                source=str(row.get("source") or ""),
            )
        )
    return tuple(samples)


def evaluate_corpus(
    samples: Iterable[CorpusSample],
    solver_name: str,
    solve_image,
) -> tuple[EvalSummary, tuple[EvalSampleResult, ...]]:
    results: list[EvalSampleResult] = []
    for sample in samples:
        started = time.perf_counter()
        solve_result: SolveResult = solve_image(sample.image)
        elapsed_ms = (time.perf_counter() - started) * 1000
        answer = solve_result.best.text if solve_result.best else ""
        answer = normalize_ocr_text(answer)
        distance = levenshtein(answer, sample.expected)
        cer = distance / max(len(sample.expected), 1)
        results.append(
            EvalSampleResult(
                id=sample.id,
                image=str(sample.image),
                expected=sample.expected,
                answer=answer,
                exact=answer == sample.expected,
                char_distance=distance,
                cer=cer,
                elapsed_ms=elapsed_ms,
                solver=solver_name,
                source=sample.source,
            )
        )

    return summarize_eval(results), tuple(results)


def summarize_eval(results: Iterable[EvalSampleResult]) -> EvalSummary:
    result_list = list(results)
    if not result_list:
        return EvalSummary(
            total=0,
            exact=0,
            sequence_accuracy=0.0,
            mean_cer=0.0,
            latency_ms_avg=0.0,
            latency_ms_p95=0.0,
        )

    latencies = [result.elapsed_ms for result in result_list]
    exact = sum(1 for result in result_list if result.exact)
    return EvalSummary(
        total=len(result_list),
        exact=exact,
        sequence_accuracy=exact / len(result_list),
        mean_cer=sum(result.cer for result in result_list) / len(result_list),
        latency_ms_avg=sum(latencies) / len(latencies),
        latency_ms_p95=percentile(latencies, 95),
    )


def levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile_value / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
