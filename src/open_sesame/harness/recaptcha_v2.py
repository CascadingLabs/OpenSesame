"""reCAPTCHA v2 live-session actor primitives for VoidCrawl pages."""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from open_sesame.harness.page_sync import wait_page_op
from open_sesame.solvers.image_classification import (
    HuggingFaceImageClassifier,
    ImageClassificationTask,
    ImageClassifierConfig,
    best_label,
    split_image_grid,
)


# Process-wide classifier cache so a multi-round solve loads each model once
# instead of once per round. Keyed by the frozen config; references the
# module-level HuggingFaceImageClassifier so tests can still monkeypatch it.
_TILE_CLASSIFIER_CACHE: dict[ImageClassifierConfig, HuggingFaceImageClassifier] = {}


def get_tile_classifier(config: ImageClassifierConfig) -> HuggingFaceImageClassifier:
    classifier = _TILE_CLASSIFIER_CACHE.get(config)
    if classifier is None:
        classifier = HuggingFaceImageClassifier(config)
        _TILE_CLASSIFIER_CACHE[config] = classifier
    return classifier


RECAPTCHA_ANCHOR_SELECTORS = (
    'iframe[src*="recaptcha/api2/anchor"]',
    'iframe[src*="google.com/recaptcha"]',
    ".g-recaptcha",
)
RECAPTCHA_CHALLENGE_SELECTORS = (
    'iframe[title*="recaptcha challenge" i]',
    'iframe[title*="challenge" i]',
    'iframe[src*="recaptcha/api2/bframe"]',
    'iframe[src*="google.com/recaptcha/api2/bframe"]',
)
TOKEN_JS = """
(() => {
  const el = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
  return el ? (el.value || el.textContent || '') : '';
})()
"""
RECAPTCHA_RATE_LIMIT_PHRASES = (
    "try again later",
    "automated queries",
)
TileAugmentationPreset = Literal["none", "helpful", "denoise"]
HELPFUL_TILE_AUGMENTATIONS = (
    "identity",
    "contrast_1_25",
    "sharpness_1_5",
    "brightness_1_10",
    "center_crop_zoom_1_15",
)
# Adversarial reCAPTCHA tiles (e.g. the 2captcha demos) add high-frequency
# speckle that suppresses CLIP confidence on genuine objects. A median pass
# recovers 3-4x of the lost score on weak true tiles without inflating
# distractors; pairing identity + median lets the ensemble keep both reads.
DENOISE_TILE_AUGMENTATIONS = (
    "identity",
    "median_3",
    "median_5",
)


@dataclass(frozen=True)
class WidgetRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0

    def as_dict(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class HumanClickTrace:
    target_x: float
    target_y: float
    points: tuple[tuple[float, float], ...]
    hold_ms: int

    def as_dict(self) -> dict[str, object]:
        return {
            "target_x": self.target_x,
            "target_y": self.target_y,
            "points": [[x, y] for x, y in self.points],
            "hold_ms": self.hold_ms,
        }


@dataclass(frozen=True)
class RecaptchaV2State:
    kind: str | None
    token: str | None
    solved: bool
    anchor_rect: WidgetRect | None = None
    challenge_rect: WidgetRect | None = None
    click_trace: HumanClickTrace | None = None
    screenshot_path: str | None = None
    challenge_image_path: str | None = None
    prompt_image_path: str | None = None
    prompt_text: str = ""
    target_label: str | None = None
    signals: tuple[str, ...] = field(default_factory=tuple)
    elapsed_ms: float = 0.0
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "token_present": bool(self.token),
            "token_length": len(self.token or ""),
            "solved": self.solved,
            "anchor_rect": self.anchor_rect.as_dict() if self.anchor_rect else None,
            "challenge_rect": self.challenge_rect.as_dict() if self.challenge_rect else None,
            "click_trace": self.click_trace.as_dict() if self.click_trace else None,
            "screenshot_path": self.screenshot_path,
            "challenge_image_path": self.challenge_image_path,
            "prompt_image_path": self.prompt_image_path,
            "prompt_text": self.prompt_text,
            "target_label": self.target_label,
            "signals": list(self.signals),
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class TileDecision:
    row: int
    col: int
    label: str
    score: float
    click_x: float
    click_y: float

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row,
            "col": self.col,
            "label": self.label,
            "score": self.score,
            "click_x": self.click_x,
            "click_y": self.click_y,
        }


@dataclass(frozen=True)
class BinaryTileVote:
    row: int
    col: int
    model_id: str
    target_label: str
    target_score: float
    non_target_label: str
    non_target_score: float
    augmentation_id: str = "identity"
    source_tile_path: str | None = None
    augmented_tile_path: str | None = None

    @property
    def votes_target(self) -> bool:
        return self.target_score > self.non_target_score

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row,
            "col": self.col,
            "model_id": self.model_id,
            "target_label": self.target_label,
            "target_score": self.target_score,
            "non_target_label": self.non_target_label,
            "non_target_score": self.non_target_score,
            "votes_target": self.votes_target,
            "augmentation_id": self.augmentation_id,
            "source_tile_path": self.source_tile_path,
            "augmented_tile_path": self.augmented_tile_path,
        }


@dataclass(frozen=True)
class EnsembleTileDecision:
    row: int
    col: int
    target_votes: int
    total_votes: int
    consensus: float
    click_x: float
    click_y: float
    votes: tuple[BinaryTileVote, ...]

    def as_tile_decision(self, *, label: str) -> TileDecision:
        return TileDecision(
            row=self.row,
            col=self.col,
            label=label,
            score=self.consensus,
            click_x=self.click_x,
            click_y=self.click_y,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row,
            "col": self.col,
            "target_votes": self.target_votes,
            "total_votes": self.total_votes,
            "consensus": self.consensus,
            "click_x": self.click_x,
            "click_y": self.click_y,
            "votes": [vote.as_dict() for vote in self.votes],
        }


@dataclass(frozen=True)
class TileVisualState:
    row: int
    col: int
    white_ratio: float
    mean_luma: float
    luma_stddev: float
    active: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row,
            "col": self.col,
            "white_ratio": self.white_ratio,
            "mean_luma": self.mean_luma,
            "luma_stddev": self.luma_stddev,
            "active": self.active,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TileGrid:
    x: float
    y: float
    width: float
    height: float
    rows: int = 3
    cols: int = 3

    def tile_center(self, row: int, col: int) -> tuple[float, float]:
        return (
            self.x + ((col + 0.5) * self.width / self.cols),
            self.y + ((row + 0.5) * self.height / self.rows),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "rows": self.rows,
            "cols": self.cols,
        }


@dataclass(frozen=True)
class RecaptchaAudioChallenge:
    clicked: bool
    click_method: str
    button_name: str
    click_trace: HumanClickTrace | None = None
    screenshot_path: str | None = None
    download_path: str | None = None
    download_bytes: int | None = None
    download_content_type: str | None = None
    rate_limited: bool = False
    signals: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "clicked": self.clicked,
            "click_method": self.click_method,
            "button_name": self.button_name,
            "click_trace": self.click_trace.as_dict() if self.click_trace else None,
            "screenshot_path": self.screenshot_path,
            "download_path": self.download_path,
            "download_bytes": self.download_bytes,
            "download_content_type": self.download_content_type,
            "rate_limited": self.rate_limited,
            "signals": list(self.signals),
            "error": self.error,
        }


@dataclass(frozen=True)
class RecaptchaResearchReport:
    """Human-readable summary of one live-session reCAPTCHA observation."""

    observed_system: dict[str, object]
    live_session: dict[str, object]
    vision: dict[str, object]
    actions: dict[str, object]
    artifacts: dict[str, object]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "observed_system": self.observed_system,
            "live_session": self.live_session,
            "vision": self.vision,
            "actions": self.actions,
            "artifacts": self.artifacts,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class RecaptchaFailureExample:
    """Local corpus entry produced from a failed live reCAPTCHA attempt."""

    example_id: str
    metadata_path: Path
    run_dir: Path
    challenge_image_path: Path | None
    prompt_image_path: Path | None
    target_label: str | None
    prompt_text: str
    outcome: str
    notes: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "example_id": self.example_id,
            "metadata_path": str(self.metadata_path),
            "run_dir": str(self.run_dir),
            "challenge_image_path": str(self.challenge_image_path) if self.challenge_image_path else None,
            "prompt_image_path": str(self.prompt_image_path) if self.prompt_image_path else None,
            "target_label": self.target_label,
            "prompt_text": self.prompt_text,
            "outcome": self.outcome,
            "notes": self.notes,
        }


def rect_lookup_js(selectors: tuple[str, ...]) -> str:
    selector_list = ", ".join(repr(selector) for selector in selectors)
    return f"""
(() => {{
  function rectOf(el) {{
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return null;
    return {{ x: r.left, y: r.top, width: r.width, height: r.height }};
  }}
  for (const selector of [{selector_list}]) {{
    const rect = rectOf(document.querySelector(selector));
    if (rect) return rect;
  }}
  return null;
}})()
"""


def recaptcha_challenge_rect_js() -> str:
    return f"""
(() => {{
  function rectOf(el) {{
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width < 120 || r.height < 120) return null;
    return {{ x: r.left, y: r.top, width: r.width, height: r.height }};
  }}
  const selectors = [{", ".join(repr(selector) for selector in RECAPTCHA_CHALLENGE_SELECTORS)}];
  for (const selector of selectors) {{
    const rect = rectOf(document.querySelector(selector));
    if (rect) return rect;
  }}
  const frames = Array.from(document.querySelectorAll('iframe'))
    .map((frame) => {{
      const rect = rectOf(frame);
      const haystack = `${{frame.src || ''}} ${{frame.title || ''}}`.toLowerCase();
      return {{ frame, rect, haystack }};
    }})
    .filter((entry) => entry.rect && /recaptcha|challenge/.test(entry.haystack))
    .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
  return frames[0]?.rect || null;
}})()
"""


def parse_widget_rect(value: object) -> WidgetRect | None:
    if not isinstance(value, dict):
        return None
    try:
        return WidgetRect(
            x=float(value["x"]),
            y=float(value["y"]),
            width=float(value["width"]),
            height=float(value["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def recaptcha_checkbox_point(
    rect: WidgetRect,
    *,
    offset_x: float = 28.0,
    seed: int = 17,
) -> tuple[float, float]:
    rng = random.Random(seed)
    jitter_x = rng.uniform(-1.8, 1.8)
    jitter_y = rng.uniform(-1.8, 1.8)
    return rect.x + offset_x + jitter_x, rect.center_y + jitter_y


def recaptcha_tile_grid_rect(challenge_rect: WidgetRect, *, rows: int = 3, cols: int = 3) -> TileGrid:
    """Estimate the image grid area inside Google's reCAPTCHA challenge card."""

    horizontal_padding = max(6.0, challenge_rect.width * 0.018)
    header_height = min(128.0, max(96.0, challenge_rect.height * 0.22))
    grid_size = min(challenge_rect.width - (horizontal_padding * 2), challenge_rect.height - header_height - 64.0)
    return TileGrid(
        x=challenge_rect.x + horizontal_padding,
        y=challenge_rect.y + header_height,
        width=grid_size,
        height=grid_size,
        rows=rows,
        cols=cols,
    )


def recaptcha_prompt_rect(challenge_rect: WidgetRect) -> WidgetRect:
    header_height = min(128.0, max(96.0, challenge_rect.height * 0.22))
    return WidgetRect(
        x=challenge_rect.x,
        y=challenge_rect.y,
        width=challenge_rect.width,
        height=header_height,
    )


def infer_tile_grid_shape(image_path: str | Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as exc:
        msg = "Install the 'ml' extra to infer reCAPTCHA grid shape."
        raise RuntimeError(msg) from exc

    image = Image.open(image_path).convert("RGB")
    return (
        _infer_grid_axis(image, axis="y"),
        _infer_grid_axis(image, axis="x"),
    )


def _infer_grid_axis(image: object, *, axis: str) -> int:
    width, height = image.size
    limit = height if axis == "y" else width
    orthogonal = width if axis == "y" else height
    line_positions: list[int] = []
    for pos in range(limit):
        light = 0
        for other in range(orthogonal):
            pixel = image.getpixel((other, pos) if axis == "y" else (pos, other))
            r, g, b = pixel[:3]
            if r >= 235 and g >= 235 and b >= 235:
                light += 1
        if light / orthogonal >= 0.55:
            line_positions.append(pos)

    clusters: list[list[int]] = []
    for pos in line_positions:
        if not clusters or pos - clusters[-1][-1] > 3:
            clusters.append([pos])
        else:
            clusters[-1].append(pos)

    internal = [
        cluster
        for cluster in clusters
        if 4 < (sum(cluster) / len(cluster)) < (limit - 5)
    ]
    count = len(internal) + 1
    if count in {3, 4}:
        return count
    return 4 if max(width, height) >= 360 else 3


def infer_tile_grid_shape_with_python(
    image_path: str | Path,
    *,
    ml_python: str | None = None,
) -> tuple[int, int]:
    python = ml_python or os.environ.get("OPENSESAME_ML_PYTHON") or "python"
    code = """
import json
import sys
from open_sesame.harness.recaptcha_v2 import infer_tile_grid_shape

print(json.dumps(infer_tile_grid_shape(sys.argv[1])))
"""
    try:
        completed = subprocess.run(
            [python, "-c", code, str(image_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        msg = f"Could not infer grid shape with {python!r}: {detail.strip()}"
        raise RuntimeError(msg) from exc
    rows, cols = json.loads(completed.stdout)
    return int(rows), int(cols)


def inspect_tile_visual_states(
    image_path: str | Path,
    *,
    rows: int,
    cols: int,
) -> tuple[TileVisualState, ...]:
    try:
        from PIL import Image
    except ImportError as exc:
        msg = "Install the 'ml' extra to inspect reCAPTCHA tile state."
        raise RuntimeError(msg) from exc

    image = Image.open(image_path).convert("RGB")
    tile_width = image.width // cols
    tile_height = image.height // rows
    states: list[TileVisualState] = []
    for row in range(rows):
        for col in range(cols):
            left = col * tile_width
            top = row * tile_height
            right = image.width if col == cols - 1 else left + tile_width
            bottom = image.height if row == rows - 1 else top + tile_height
            tile = image.crop((left, top, right, bottom))
            state = inspect_tile_image_state(tile, row=row, col=col)
            states.append(state)
    return tuple(states)


def inspect_tile_image_state(image: object, *, row: int, col: int) -> TileVisualState:
    pixels = list(image.getdata())
    if not pixels:
        return TileVisualState(row, col, 1.0, 255.0, 0.0, False, "empty")
    lumas: list[float] = []
    white = 0
    for pixel in pixels:
        r, g, b = pixel[:3]
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        lumas.append(luma)
        if r >= 245 and g >= 245 and b >= 245:
            white += 1
    mean = sum(lumas) / len(lumas)
    variance = sum((value - mean) ** 2 for value in lumas) / len(lumas)
    stddev = math.sqrt(variance)
    white_ratio = white / len(pixels)
    if white_ratio >= 0.25 and mean >= 100:
        return TileVisualState(row, col, white_ratio, mean, stddev, False, "selected_or_blank")
    if mean >= 225 and stddev <= 12.0:
        return TileVisualState(row, col, white_ratio, mean, stddev, False, "selected_or_blank")
    if white_ratio >= 0.52 and mean >= 215:
        return TileVisualState(row, col, white_ratio, mean, stddev, False, "selected_or_blank")
    if stddev <= 4.0:
        return TileVisualState(row, col, white_ratio, mean, stddev, False, "low_detail")
    return TileVisualState(row, col, white_ratio, mean, stddev, True, "active")


def inspect_tile_visual_states_with_python(
    image_path: str | Path,
    *,
    rows: int,
    cols: int,
    ml_python: str | None = None,
) -> tuple[TileVisualState, ...]:
    python = ml_python or os.environ.get("OPENSESAME_ML_PYTHON") or "python"
    code = """
import json
import sys
from open_sesame.harness.recaptcha_v2 import inspect_tile_visual_states

states = inspect_tile_visual_states(sys.argv[1], rows=int(sys.argv[2]), cols=int(sys.argv[3]))
print(json.dumps([state.as_dict() for state in states]))
"""
    try:
        completed = subprocess.run(
            [python, "-c", code, str(image_path), str(rows), str(cols)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        msg = f"Could not inspect tile states with {python!r}: {detail.strip()}"
        raise RuntimeError(msg) from exc
    return tuple(tile_visual_state_from_dict(item) for item in json.loads(completed.stdout))


def tile_visual_state_from_dict(item: dict[str, object]) -> TileVisualState:
    return TileVisualState(
        row=int(item["row"]),
        col=int(item["col"]),
        white_ratio=float(item["white_ratio"]),
        mean_luma=float(item["mean_luma"]),
        luma_stddev=float(item["luma_stddev"]),
        active=bool(item["active"]),
        reason=str(item["reason"]),
    )


def human_mouse_path(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    steps: int = 18,
    seed: int = 17,
) -> tuple[tuple[float, float], ...]:
    if steps < 2:
        msg = "steps must be at least 2"
        raise ValueError(msg)
    rng = random.Random(seed)
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    distance = math.hypot(dx, dy) or 1.0
    normal_x = -dy / distance
    normal_y = dx / distance
    curve = rng.uniform(-22.0, 22.0)
    points: list[tuple[float, float]] = []
    for index in range(steps):
        t = index / (steps - 1)
        ease = t * t * (3 - 2 * t)
        arc = math.sin(math.pi * t) * curve
        wobble = rng.uniform(-0.9, 0.9) if 0 < index < steps - 1 else 0.0
        x = sx + dx * ease + normal_x * arc + wobble
        y = sy + dy * ease + normal_y * arc + wobble
        points.append((x, y))
    return tuple(points)


async def click_like_human(
    page: object,
    target_x: float,
    target_y: float,
    *,
    start: tuple[float, float] = (140.0, 180.0),
    steps: int = 18,
    seed: int = 17,
    hold_ms: int = 80,
    move_delay: float = 0.018,
) -> HumanClickTrace:
    points = human_mouse_path(start, (target_x, target_y), steps=steps, seed=seed)
    for x, y in points:
        await page.dispatch_mouse_event("mouseMoved", x, y)
        if move_delay > 0:
            await asyncio.sleep(move_delay)
    await page.dispatch_mouse_event("mousePressed", target_x, target_y, button="left", click_count=1)
    await asyncio.sleep(hold_ms / 1000.0)
    await page.dispatch_mouse_event("mouseReleased", target_x, target_y, button="left", click_count=1)
    return HumanClickTrace(target_x=target_x, target_y=target_y, points=points, hold_ms=hold_ms)


async def attempt_recaptcha_v2_checkbox(
    page: object,
    *,
    wait_secs: float = 20.0,
    screenshot_path: str | Path | None = None,
    challenge_image_path: str | Path | None = None,
    prompt_image_path: str | Path | None = None,
    ml_python: str | None = None,
    tesseract_cmd: str = "tesseract",
    seed: int = 17,
) -> RecaptchaV2State:
    started = time.perf_counter()
    signals: list[str] = []
    try:
        kind = await _detect_captcha(page)
        if kind:
            signals.append(f"captcha-{kind}")

        anchor_rect = parse_widget_rect(await page.eval_js(rect_lookup_js(RECAPTCHA_ANCHOR_SELECTORS)))
        if anchor_rect is None:
            if kind is None:
                signals.append("no-captcha-detected")
            else:
                signals.append("anchor-not-found")
            return RecaptchaV2State(
                kind=kind,
                token=None,
                solved=kind is None,
                signals=tuple(signals),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        target_x, target_y = recaptcha_checkbox_point(anchor_rect, seed=seed)
        trace = await click_like_human(page, target_x, target_y, seed=seed)
        token, solved = await wait_for_recaptcha_result(page, wait_secs=wait_secs)
        if token:
            signals.append("token-observed")
        elif solved:
            signals.append("captcha-detector-cleared")

        challenge_rect = parse_widget_rect(await page.eval_js(recaptcha_challenge_rect_js()))
        if challenge_rect is not None:
            signals.append("challenge-frame-visible")

        shot = None
        if screenshot_path is not None:
            shot = str(Path(screenshot_path).expanduser().resolve())
            Path(shot).parent.mkdir(parents=True, exist_ok=True)
            Path(shot).write_bytes(await page.screenshot_png())

        challenge = None
        prompt_image = None
        prompt_text = ""
        target_label = None
        if challenge_image_path is not None and challenge_rect is not None:
            challenge = str(Path(challenge_image_path).expanduser().resolve())
            try:
                await crop_page_screenshot(
                    page,
                    recaptcha_tile_grid_rect(challenge_rect),
                    challenge,
                    ml_python=ml_python,
                )
                signals.append("challenge-grid-cropped")
            except RuntimeError as exc:
                challenge = None
                signals.append(f"challenge-grid-crop-unavailable:{exc}")
        if prompt_image_path is not None and challenge_rect is not None:
            prompt_image = str(Path(prompt_image_path).expanduser().resolve())
            try:
                await crop_page_screenshot(
                    page,
                    recaptcha_prompt_rect(challenge_rect),
                    prompt_image,
                    ml_python=ml_python,
                )
                prompt_text = ocr_recaptcha_prompt(prompt_image, tesseract_cmd=tesseract_cmd)
                target_label = parse_recaptcha_target_label(prompt_text)
                signals.append("prompt-ocr-complete" if target_label else "prompt-ocr-no-target")
            except RuntimeError as exc:
                prompt_image = None
                signals.append(f"prompt-ocr-unavailable:{exc}")

        return RecaptchaV2State(
            kind=kind,
            token=token,
            solved=solved,
            anchor_rect=anchor_rect,
            challenge_rect=challenge_rect,
            click_trace=trace,
            screenshot_path=shot,
            challenge_image_path=challenge,
            prompt_image_path=prompt_image,
            prompt_text=prompt_text,
            target_label=target_label,
            signals=tuple(signals),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception as exc:  # pragma: no cover - live browser path
        return RecaptchaV2State(
            kind=None,
            token=None,
            solved=False,
            signals=tuple(signals),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )


async def wait_for_recaptcha_result(page: object, *, wait_secs: float) -> tuple[str | None, bool]:
    deadline = time.perf_counter() + wait_secs
    while time.perf_counter() < deadline:
        token = str(await page.eval_js(TOKEN_JS) or "")
        if token:
            return token, True
        if await _detect_captcha(page) is None:
            return None, True
        await asyncio.sleep(0.5)
    token = str(await page.eval_js(TOKEN_JS) or "")
    return (token or None), bool(token)


async def click_tile_decisions(
    page: object,
    grid: TileGrid,
    decisions: tuple[TileDecision, ...],
    *,
    seed: int = 41,
) -> tuple[HumanClickTrace, ...]:
    traces: list[HumanClickTrace] = []
    start = (grid.x + grid.width + 40.0, grid.y + grid.height + 40.0)
    for index, decision in enumerate(decisions):
        x, y = grid.tile_center(decision.row, decision.col)
        trace = await click_like_human(
            page,
            x,
            y,
            start=start,
            steps=10,
            seed=seed + index,
            hold_ms=55,
            move_delay=0.012,
        )
        traces.append(trace)
        start = (x, y)
    return tuple(traces)


async def click_recaptcha_verify_button(
    page: object,
    challenge_rect: WidgetRect,
    *,
    seed: int = 73,
) -> HumanClickTrace:
    x = challenge_rect.x + challenge_rect.width - 62.0
    y = challenge_rect.y + challenge_rect.height - 31.0
    return await click_like_human(
        page,
        x,
        y,
        start=(x - 90.0, y - 30.0),
        steps=9,
        seed=seed,
        hold_ms=70,
        move_delay=0.012,
    )


def recaptcha_audio_button_point(challenge_rect: WidgetRect) -> tuple[float, float]:
    """Approximate Google's bottom-row audio challenge control inside the challenge card."""

    return challenge_rect.x + 76.0, challenge_rect.y + challenge_rect.height - 31.0


async def click_recaptcha_audio_button(
    page: object,
    challenge_rect: WidgetRect,
    *,
    seed: int = 91,
    action_timeout: float = 8.0,
) -> tuple[str, str, HumanClickTrace | None]:
    """Press the audio challenge control using AX when possible, then coordinates."""

    button_name = await find_recaptcha_ax_control_name(
        page,
        roles=("button",),
        includes=("audio",),
        timeout=action_timeout,
    )
    if button_name:
        try:
            await wait_page_op(page.click_by_role("button", button_name), timeout=action_timeout)
            return "ax", button_name, None
        except (TimeoutError, Exception):
            pass

    x, y = recaptcha_audio_button_point(challenge_rect)
    trace = await click_like_human(
        page,
        x,
        y,
        start=(x - 42.0, y - 18.0),
        steps=8,
        seed=seed,
        hold_ms=70,
        move_delay=0.012,
    )
    return "coordinate", "", trace


async def attempt_recaptcha_audio_challenge(
    page: object,
    challenge_rect: WidgetRect,
    *,
    screenshot_path: str | Path | None = None,
    download_dir: str | Path | None = None,
    download_timeout: float = 20.0,
    seed: int = 91,
    action_timeout: float = 8.0,
    tesseract_cmd: str = "tesseract",
) -> RecaptchaAudioChallenge:
    signals: list[str] = []
    try:
        click_method, button_name, click_trace = await click_recaptcha_audio_button(
            page,
            challenge_rect,
            seed=seed,
            action_timeout=action_timeout,
        )
        signals.append(f"audio-button-clicked:{click_method}")
        await asyncio.sleep(1.0)

        rate_limited = await detect_recaptcha_rate_limit(
            page,
            timeout=action_timeout,
            tesseract_cmd=tesseract_cmd,
        )
        if rate_limited:
            signals.append("audio-rate-limited")

        shot = None
        if screenshot_path is not None:
            shot = str(Path(screenshot_path).expanduser().resolve())
            Path(shot).parent.mkdir(parents=True, exist_ok=True)
            Path(shot).write_bytes(await page.screenshot_png())
            signals.append("audio-screenshot-captured")

        download_path = None
        download_bytes = None
        download_content_type = None
        if rate_limited:
            signals.append("audio-download-skipped-rate-limited")
        elif download_dir is not None:
            outcome = await download_recaptcha_audio_challenge(
                page,
                download_dir=download_dir,
                timeout=download_timeout,
            )
            if outcome is not None:
                download_path = str(Path(str(outcome.path)).expanduser().resolve())
                download_bytes = int(outcome.bytes)
                download_content_type = (
                    str(outcome.content_type)
                    if getattr(outcome, "content_type", None) is not None
                    else None
                )
                signals.append("audio-download-captured")
            else:
                signals.append("audio-download-unavailable")

        return RecaptchaAudioChallenge(
            clicked=True,
            click_method=click_method,
            button_name=button_name,
            click_trace=click_trace,
            screenshot_path=shot,
            download_path=download_path,
            download_bytes=download_bytes,
            download_content_type=download_content_type,
            rate_limited=rate_limited,
            signals=tuple(signals),
        )
    except Exception as exc:  # pragma: no cover - live browser path
        return RecaptchaAudioChallenge(
            clicked=False,
            click_method="",
            button_name="",
            signals=tuple(signals),
            error=f"{type(exc).__name__}: {exc}",
        )


async def download_recaptcha_audio_challenge(
    page: object,
    *,
    download_dir: str | Path,
    timeout: float = 20.0,
) -> object | None:
    """Capture Google's audio challenge download, if the AX tree exposes the link."""

    control = await find_recaptcha_ax_control(
        page,
        roles=("link", "button"),
        includes=("download",),
        timeout=timeout,
    )
    if control is None:
        return None
    role, name = control
    output = Path(download_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    capture = await page.arm_download(str(output), max_bytes=8_000_000)
    try:
        await wait_page_op(page.click_by_role(role, name), timeout=timeout)
        return await wait_page_op(page.wait_download(capture, timeout=timeout), timeout=timeout + 1.0)
    except Exception:
        try:
            await page.reset_download()
        except Exception:
            pass
        return None


async def detect_recaptcha_rate_limit(
    page: object,
    *,
    timeout: float = 8.0,
    tesseract_cmd: str = "tesseract",
) -> bool:
    """Report whether Google's "try again later" block is on screen.

    CAS-178 requires block-rate to be reported separately from
    transcription accuracy: a rate-limited session has no audio to
    transcribe, and treating it as a download failure hides the block.
    """

    try:
        nodes = await wait_page_op(page.get_full_ax_tree(), timeout=timeout)
    except (TimeoutError, Exception):
        nodes = None
    if ax_nodes_mention_rate_limit(nodes):
        return True

    # The challenge lives in an iframe the AX tree does not descend into,
    # so fall back to OCR over the full-page screenshot.
    try:
        png = await wait_page_op(page.screenshot_png(), timeout=timeout)
    except (TimeoutError, Exception):
        return False
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(png)
        tmp_path = Path(tmp.name)
    try:
        text = ocr_recaptcha_prompt(tmp_path, tesseract_cmd=tesseract_cmd)
    except RuntimeError:
        return False
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    return text_mentions_rate_limit(text)


def ax_nodes_mention_rate_limit(nodes: object) -> bool:
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        text = " ".join(
            part
            for part in (
                ax_value(node.get("name")),
                ax_value(node.get("value")),
                ax_value(node.get("description")),
            )
            if part
        )
        if text_mentions_rate_limit(text):
            return True
    return False


def text_mentions_rate_limit(text: str) -> bool:
    lowered = re.sub(r"\s+", " ", text.lower())
    return any(phrase in lowered for phrase in RECAPTCHA_RATE_LIMIT_PHRASES)


async def find_recaptcha_ax_control_name(
    page: object,
    *,
    roles: tuple[str, ...],
    includes: tuple[str, ...],
    excludes: tuple[str, ...] = (),
    timeout: float = 8.0,
) -> str | None:
    control = await find_recaptcha_ax_control(
        page,
        roles=roles,
        includes=includes,
        excludes=excludes,
        timeout=timeout,
    )
    return control[1] if control is not None else None


async def find_recaptcha_ax_control(
    page: object,
    *,
    roles: tuple[str, ...],
    includes: tuple[str, ...],
    excludes: tuple[str, ...] = (),
    timeout: float = 8.0,
) -> tuple[str, str] | None:
    try:
        nodes = await wait_page_op(page.get_full_ax_tree(), timeout=timeout)
    except (TimeoutError, Exception):
        return None
    role_set = {role.lower() for role in roles}
    include_terms = tuple(term.lower() for term in includes)
    exclude_terms = tuple(term.lower() for term in excludes)
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        role = ax_value(node.get("role")).lower()
        name = ax_value(node.get("name")).strip()
        lowered = name.lower()
        if (
            role in role_set
            and name
            and all(term in lowered for term in include_terms)
            and not any(term in lowered for term in exclude_terms)
        ):
            return role, name
    return None


def ax_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("value")
        return str(inner) if inner is not None else ""
    return ""


async def collect_recaptcha_page_metadata(page: object) -> dict[str, object]:
    data = await page.eval_js(
        """
(() => {
  const tokenEl = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
  const token = tokenEl ? (tokenEl.value || tokenEl.textContent || '') : '';
  const anchor = document.querySelector('iframe[src*="recaptcha/api2/anchor"], iframe[src*="google.com/recaptcha"]');
  const src = anchor?.src || '';
  let sitekey = document.querySelector('.g-recaptcha[data-sitekey], [data-sitekey]')?.getAttribute('data-sitekey') || '';
  try {
    if (!sitekey && src) sitekey = new URL(src).searchParams.get('k') || '';
  } catch (_) {}
  return {
    url: location.href,
    title: document.title || '',
    sitekey,
    anchor_src: src,
    response_field_present: !!tokenEl,
    token_present: !!token,
    token_length: token.length
  };
})()
"""
    )
    return dict(data) if isinstance(data, dict) else {}


def persist_recaptcha_attempt(
    payload: dict[str, object],
    *,
    audit_dir: str | Path = ".local/recaptcha-runs",
) -> Path:
    post_verify = payload.get("post_verify") if isinstance(payload.get("post_verify"), dict) else {}
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    token_present = bool((post_verify or {}).get("token_present") or (state or {}).get("token_present"))
    outcome = "success" if token_present else "failure"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = Path(audit_dir).expanduser().resolve() / f"{timestamp}-{outcome}"
    run_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, str] = {}
    for key, value in iter_artifact_paths(payload):
        source = Path(value)
        if not source.exists():
            continue
        target = run_dir / f"{key}{source.suffix or '.png'}"
        shutil.copy2(source, target)
        artifacts[key] = str(target)

    artifacts.update(render_recaptcha_click_review_artifacts(payload, artifacts, run_dir))

    record = {
        "created_at": timestamp,
        "outcome": outcome,
        "artifacts": artifacts,
        "review": {
            "status": "unreviewed",
            "tile_labels_path": None,
            "notes": "",
        },
        "payload": payload,
    }
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    index_path = Path(audit_dir).expanduser().resolve() / "attempts.jsonl"
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"created_at": timestamp, "outcome": outcome, "path": str(metadata_path)}) + "\n")
    return metadata_path


def render_recaptcha_click_review_artifacts(
    payload: dict[str, object],
    artifacts: dict[str, str],
    run_dir: Path,
) -> dict[str, str]:
    """Render human-review overlays showing the tile clicks planned per round."""

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return {}

    rendered: dict[str, str] = {}
    rounds = payload.get("rounds")
    if not isinstance(rounds, list):
        return rendered
    for item in rounds:
        if not isinstance(item, dict):
            continue
        round_number = item.get("round")
        try:
            round_index = int(round_number)
        except (TypeError, ValueError):
            continue
        image_path = artifacts.get(f"round_{round_index}_challenge")
        if not image_path:
            continue
        output = run_dir / f"round_{round_index}_click_overlay.png"
        try:
            render_recaptcha_click_overlay(
                Path(image_path),
                item,
                output,
                image_module=Image,
                draw_module=ImageDraw,
                font_module=ImageFont,
            )
        except (OSError, ValueError):
            continue
        rendered[f"round_{round_index}_click_overlay"] = str(output)
    return rendered


def render_recaptcha_click_overlay(
    image_path: Path,
    round_record: dict[str, object],
    output_path: Path,
    *,
    image_module: object,
    draw_module: object,
    font_module: object,
) -> None:
    image = image_module.open(image_path).convert("RGBA")
    overlay = image_module.new("RGBA", image.size, (0, 0, 0, 0))
    draw = draw_module.Draw(overlay)
    rows, cols = recaptcha_round_grid_shape(round_record)
    width, height = image.size

    for col in range(1, cols):
        x = round(width * col / cols)
        draw.line((x, 0, x, height), fill=(255, 255, 0, 180), width=2)
    for row in range(1, rows):
        y = round(height * row / rows)
        draw.line((0, y, width, y), fill=(255, 255, 0, 180), width=2)

    decisions = recaptcha_round_click_decisions(round_record)
    font = font_module.load_default()
    for index, decision in enumerate(decisions, start=1):
        draw_recaptcha_click_decision(draw, width, height, rows, cols, decision, index, font)
    if not decisions:
        draw.text((12, 12), "no clicks planned", fill=(255, 64, 64, 255), font=font)

    image_module.alpha_composite(image, overlay).convert("RGB").save(output_path)


def recaptcha_round_grid_shape(round_record: dict[str, object]) -> tuple[int, int]:
    states = round_record.get("tile_states")
    if isinstance(states, list) and states:
        rows = max(int(state.get("row", 0)) for state in states if isinstance(state, dict)) + 1
        cols = max(int(state.get("col", 0)) for state in states if isinstance(state, dict)) + 1
        return rows, cols
    decisions = recaptcha_round_click_decisions(round_record)
    if decisions:
        rows = max(int(decision.get("row", 0)) for decision in decisions) + 1
        cols = max(int(decision.get("col", 0)) for decision in decisions) + 1
        return max(rows, 3), max(cols, 3)
    return 3, 3


def recaptcha_round_click_decisions(round_record: dict[str, object]) -> tuple[dict[str, object], ...]:
    tile_plan = round_record.get("tile_plan")
    if isinstance(tile_plan, list) and tile_plan:
        return tuple(item for item in tile_plan if isinstance(item, dict))
    ensemble_plan = round_record.get("ensemble_plan")
    if not isinstance(ensemble_plan, list):
        return ()
    decisions: list[dict[str, object]] = []
    for item in ensemble_plan:
        if not isinstance(item, dict):
            continue
        votes = item.get("votes") if isinstance(item.get("votes"), list) else []
        first_vote = next((vote for vote in votes if isinstance(vote, dict)), {})
        decisions.append(
            {
                "row": item.get("row", 0),
                "col": item.get("col", 0),
                "click_x": item.get("click_x"),
                "click_y": item.get("click_y"),
                "score": item.get("consensus"),
                "label": first_vote.get("target_label", ""),
            }
        )
    return tuple(decisions)


def draw_recaptcha_click_decision(
    draw: object,
    width: int,
    height: int,
    rows: int,
    cols: int,
    decision: dict[str, object],
    index: int,
    font: object,
) -> None:
    row = int(decision.get("row", 0))
    col = int(decision.get("col", 0))
    left = round(width * col / cols)
    top = round(height * row / rows)
    right = round(width * (col + 1) / cols)
    bottom = round(height * (row + 1) / rows)
    click_x = decision.get("click_x")
    click_y = decision.get("click_y")
    cx = round(width * float(click_x if click_x is not None else (col + 0.5) / cols))
    cy = round(height * float(click_y if click_y is not None else (row + 0.5) / rows))

    draw.rectangle((left, top, right, bottom), outline=(255, 0, 0, 255), width=5)
    radius = max(12, min(width, height) // 28)
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=(255, 0, 0, 200),
        outline=(255, 255, 255, 255),
        width=2,
    )
    score = decision.get("score")
    label = f"{index}"
    if score is not None:
        label = f"{label}: {float(score):.2f}"
    target_label = decision.get("label")
    if target_label:
        label = f"{label} {target_label}"
    draw.text((left + 6, top + 6), label, fill=(255, 255, 255, 255), font=font)


def build_recaptcha_research_report(payload: dict[str, object]) -> RecaptchaResearchReport:
    """Summarize one actor payload as research evidence instead of solver magic."""

    state = _dict_or_empty(payload.get("state"))
    before = _dict_or_empty(payload.get("page_metadata_before_tiles"))
    after = _dict_or_empty(payload.get("page_metadata_after_verify"))
    post_verify = _dict_or_empty(payload.get("post_verify"))
    rounds = payload.get("rounds") if isinstance(payload.get("rounds"), list) else []
    challenges = payload.get("challenges") if isinstance(payload.get("challenges"), list) else []

    token_present_before = bool(before.get("token_present"))
    token_present_after = bool(after.get("token_present") or post_verify.get("token_present"))
    challenge_visible = bool(state.get("challenge_rect"))
    target_label = state.get("target_label")

    total_tile_clicks = 0
    active_tiles = 0
    planned_tiles = 0
    model_ids: set[str] = set()
    round_summaries: list[dict[str, object]] = []
    for item in rounds:
        if not isinstance(item, dict):
            continue
        tile_states = item.get("tile_states") if isinstance(item.get("tile_states"), list) else []
        ensemble_plan = item.get("ensemble_plan") if isinstance(item.get("ensemble_plan"), list) else []
        tile_plan = item.get("tile_plan") if isinstance(item.get("tile_plan"), list) else []
        tile_click_traces = (
            item.get("tile_click_traces")
            if isinstance(item.get("tile_click_traces"), list)
            else []
        )
        active_count = sum(1 for state_item in tile_states if isinstance(state_item, dict) and state_item.get("active"))
        click_count = len(tile_click_traces)
        active_tiles += active_count
        planned_tiles += len(tile_plan)
        total_tile_clicks += click_count
        for decision in ensemble_plan:
            if not isinstance(decision, dict):
                continue
            votes = decision.get("votes") if isinstance(decision.get("votes"), list) else []
            for vote in votes:
                if isinstance(vote, dict) and vote.get("model_id"):
                    model_ids.add(str(vote["model_id"]))
        round_summaries.append(
            {
                "round": item.get("round"),
                "challenge": item.get("challenge"),
                "challenge_type": item.get("challenge_type"),
                "target_label": item.get("target_label"),
                "challenge_image_path": item.get("challenge_image_path"),
                "active_tiles": active_count,
                "planned_tiles": len(tile_plan),
                "clicked_tiles": click_count,
                "ensemble_decisions": len(ensemble_plan),
            }
        )

    notes: list[str] = [
        "Research report: records what the live page exposed and what local vision planned.",
        "Session-bound invariant: any observed token is only meaningful in the VoidCrawl page that earned it.",
    ]
    if challenge_visible:
        notes.append("A visual challenge frame was observed after the anchor click.")
    if target_label:
        notes.append("Prompt OCR produced a target label for local tile inspection.")
    audio_challenge = _dict_or_empty(payload.get("audio_challenge"))
    if token_present_after:
        notes.append("A g-recaptcha-response value was observed in the live page after interaction.")
    else:
        notes.append("No reusable token was produced; this attempt remains inspection/training evidence.")
    if audio_challenge:
        notes.append("An audio challenge path was attempted and recorded as live-session evidence.")
    if audio_challenge.get("rate_limited"):
        notes.append(
            "Google rate-limited the audio side-door (block-rate failure, distinct from "
            "transcription accuracy); mitigation is proxy/profile rotation, not the solver."
        )

    artifacts = {
        "screenshot_path": state.get("screenshot_path"),
        "challenge_image_path": state.get("challenge_image_path"),
        "prompt_image_path": state.get("prompt_image_path"),
        "audio_challenge_screenshot_path": audio_challenge.get("screenshot_path"),
        "audio_challenge_download_path": audio_challenge.get("download_path"),
        "post_verify_screenshot_path": post_verify.get("screenshot_path"),
        "audit_record_path": payload.get("audit_record_path"),
    }

    return RecaptchaResearchReport(
        observed_system={
            "captcha_kind": state.get("kind") or post_verify.get("captcha_kind"),
            "sitekey": before.get("sitekey") or after.get("sitekey") or "",
            "anchor_src": before.get("anchor_src") or after.get("anchor_src") or "",
            "response_field_present": bool(
                before.get("response_field_present") or after.get("response_field_present")
            ),
            "challenge_frame_visible": challenge_visible,
            "prompt_text": state.get("prompt_text") or "",
            "target_label": target_label,
            "signals": state.get("signals") if isinstance(state.get("signals"), list) else [],
        },
        live_session={
            "query": payload.get("query"),
            "target_url": payload.get("target_url"),
            "page_url_before": before.get("url") or "",
            "page_title_before": before.get("title") or "",
            "page_url_after": after.get("url") or "",
            "page_title_after": after.get("title") or "",
            "token_present_before": token_present_before,
            "token_present_after": token_present_after,
            "token_length_after": int(after.get("token_length") or post_verify.get("token_length") or 0),
            "session_bound": True,
        },
        vision={
            "model_ids": sorted(model_ids),
            "target_label": target_label,
            "rounds": round_summaries,
            "challenges": [item for item in challenges if isinstance(item, dict)],
            "active_tiles": active_tiles,
            "planned_tiles": planned_tiles,
            "clicked_tiles": total_tile_clicks,
        },
        actions={
            "checkbox_clicked": bool(state.get("click_trace")),
            "tile_rounds": len(round_summaries),
            "tile_clicks": total_tile_clicks,
            "audio_clicked": bool(audio_challenge.get("clicked")),
            "audio_click_method": audio_challenge.get("click_method") or "",
            "audio_rate_limited": bool(audio_challenge.get("rate_limited")),
            "audio_download_captured": bool(audio_challenge.get("download_path")),
            "verify_clicked": bool(payload.get("verify_click_trace")),
            "post_verify_captcha_kind": post_verify.get("captcha_kind"),
        },
        artifacts=artifacts,
        notes=tuple(notes),
    )


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def discover_recaptcha_failure_examples(
    audit_dir: str | Path = ".local/recaptcha-runs",
) -> tuple[RecaptchaFailureExample, ...]:
    """Find failed live attempts that have reusable local challenge artifacts."""

    root = Path(audit_dir).expanduser().resolve()
    if not root.exists():
        return ()

    examples: list[RecaptchaFailureExample] = []
    seen: set[Path] = set()
    for metadata_path in _iter_recaptcha_metadata_paths(root):
        metadata_path = metadata_path.resolve()
        if metadata_path in seen:
            continue
        seen.add(metadata_path)
        try:
            record = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("outcome") != "failure":
            continue
        example = recaptcha_failure_example_from_record(record, metadata_path=metadata_path)
        if example is not None:
            examples.append(example)
    return tuple(sorted(examples, key=lambda example: example.example_id))


def _iter_recaptcha_metadata_paths(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    index_path = root / "attempts.jsonl"
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            path = item.get("path")
            if isinstance(path, str) and path:
                paths.append(Path(path))
    paths.extend(root.glob("*/metadata.json"))
    return tuple(paths)


def recaptcha_failure_example_from_record(
    record: dict[str, object],
    *,
    metadata_path: Path,
) -> RecaptchaFailureExample | None:
    payload = _dict_or_empty(record.get("payload"))
    state = _dict_or_empty(payload.get("state"))
    artifacts = _dict_or_empty(record.get("artifacts"))
    challenge = _first_existing_path(
        metadata_path.parent,
        artifacts.get("challenge_image"),
        state.get("challenge_image_path"),
    )
    if challenge is None:
        return None
    prompt = _first_existing_path(
        metadata_path.parent,
        artifacts.get("prompt_image"),
        state.get("prompt_image_path"),
    )
    created = str(record.get("created_at") or metadata_path.parent.name)
    safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "-", created).strip("-") or metadata_path.parent.name
    return RecaptchaFailureExample(
        example_id=safe_id,
        metadata_path=metadata_path,
        run_dir=metadata_path.parent,
        challenge_image_path=challenge,
        prompt_image_path=prompt,
        target_label=str(state["target_label"]) if state.get("target_label") else None,
        prompt_text=str(state.get("prompt_text") or ""),
        outcome=str(record.get("outcome") or "failure"),
        notes=str(_dict_or_empty(record.get("review")).get("notes") or ""),
    )


def _first_existing_path(base_dir: Path, *values: object) -> Path | None:
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        path = Path(value).expanduser()
        candidates = [path]
        if not path.is_absolute():
            candidates.append(base_dir / path)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
    return None


def export_recaptcha_failure_corpus(
    audit_dir: str | Path = ".local/recaptcha-runs",
    corpus_dir: str | Path = ".local/recaptcha-failures",
) -> tuple[RecaptchaFailureExample, ...]:
    """Copy failed attempt artifacts into a stable local corpus directory."""

    output = Path(corpus_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    exported: list[RecaptchaFailureExample] = []
    index_path = output / "corpus.jsonl"
    with index_path.open("w", encoding="utf-8") as index:
        for example in discover_recaptcha_failure_examples(audit_dir):
            example_dir = output / example.example_id
            example_dir.mkdir(parents=True, exist_ok=True)
            challenge_path = None
            prompt_path = None
            if example.challenge_image_path is not None:
                challenge_path = example_dir / f"challenge{example.challenge_image_path.suffix or '.png'}"
                shutil.copy2(example.challenge_image_path, challenge_path)
            if example.prompt_image_path is not None:
                prompt_path = example_dir / f"prompt{example.prompt_image_path.suffix or '.png'}"
                shutil.copy2(example.prompt_image_path, prompt_path)
            metadata_copy = example_dir / "metadata.json"
            shutil.copy2(example.metadata_path, metadata_copy)
            exported_example = RecaptchaFailureExample(
                example_id=example.example_id,
                metadata_path=metadata_copy,
                run_dir=example_dir,
                challenge_image_path=challenge_path,
                prompt_image_path=prompt_path,
                target_label=example.target_label,
                prompt_text=example.prompt_text,
                outcome=example.outcome,
                notes=example.notes,
            )
            exported.append(exported_example)
            index.write(json.dumps(exported_example.as_dict(), sort_keys=True) + "\n")
    return tuple(exported)


def iter_artifact_paths(payload: dict[str, object]) -> tuple[tuple[str, str], ...]:
    paths: list[tuple[str, str]] = []
    state = payload.get("state")
    if isinstance(state, dict):
        for key in ("screenshot_path", "challenge_image_path", "prompt_image_path"):
            value = state.get(key)
            if isinstance(value, str) and value:
                paths.append((key.removesuffix("_path"), value))
    post_verify = payload.get("post_verify")
    if isinstance(post_verify, dict):
        value = post_verify.get("screenshot_path")
        if isinstance(value, str) and value:
            paths.append(("post_verify", value))
    audio = payload.get("audio_challenge")
    if isinstance(audio, dict):
        value = audio.get("screenshot_path")
        if isinstance(value, str) and value:
            paths.append(("audio_challenge_screenshot", value))
        value = audio.get("download_path")
        if isinstance(value, str) and value:
            paths.append(("audio_challenge_download", value))
    rounds = payload.get("rounds")
    if isinstance(rounds, list):
        for item in rounds:
            if not isinstance(item, dict):
                continue
            value = item.get("challenge_image_path")
            round_number = item.get("round")
            if isinstance(value, str) and value:
                paths.append((f"round_{round_number}_challenge", value))
    challenges = payload.get("challenges")
    if isinstance(challenges, list):
        for item in challenges:
            if not isinstance(item, dict):
                continue
            value = item.get("prompt_image_path")
            challenge_number = item.get("challenge")
            if isinstance(value, str) and value:
                paths.append((f"challenge_{challenge_number}_prompt", value))
    return tuple(paths)


async def crop_page_screenshot(
    page: object,
    rect: WidgetRect | TileGrid,
    output_path: str | Path,
    *,
    ml_python: str | None = None,
) -> Path:
    # Prefer a CDP bbox clip: it renders/encodes only the widget region (~0.1s)
    # instead of the whole page, which can time out under software/GPU rendering
    # in the headful container. Fall back to full screenshot + crop if the
    # clip API is unavailable or fails.
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    bbox = (max(0, int(rect.x)), max(0, int(rect.y)), max(1, int(rect.width)), max(1, int(rect.height)))
    screenshot = getattr(page, "screenshot", None)
    if callable(screenshot):
        try:
            await screenshot(path=str(output), bbox=bbox)
            if output.exists() and output.stat().st_size > 0:
                return output
        except Exception:
            pass
    png = await page.screenshot_png()
    dpr = float(await page.eval_js("window.devicePixelRatio || 1") or 1.0)
    return crop_png_bytes(png, rect, output_path, dpr=dpr, ml_python=ml_python)


def crop_png_bytes(
    png: bytes,
    rect: WidgetRect | TileGrid,
    output_path: str | Path,
    *,
    dpr: float = 1.0,
    ml_python: str | None = None,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        return crop_png_bytes_with_python(png, rect, output, dpr=dpr, ml_python=ml_python)
    import io

    image = Image.open(io.BytesIO(png)).convert("RGB")
    left = max(0, int(rect.x * dpr))
    top = max(0, int(rect.y * dpr))
    right = min(image.width, int((rect.x + rect.width) * dpr))
    bottom = min(image.height, int((rect.y + rect.height) * dpr))
    image.crop((left, top, right, bottom)).save(output)
    return output


def crop_png_bytes_with_python(
    png: bytes,
    rect: WidgetRect | TileGrid,
    output_path: Path,
    *,
    dpr: float,
    ml_python: str | None = None,
) -> Path:
    python = ml_python or os.environ.get("OPENSESAME_ML_PYTHON") or "python"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(png)
        tmp_path = Path(tmp.name)
    code = (
        "import sys; "
        "from PIL import Image; "
        "src,dst,x,y,w,h,dpr=sys.argv[1:8]; "
        "x=float(x); y=float(y); w=float(w); h=float(h); dpr=float(dpr); "
        "im=Image.open(src).convert('RGB'); "
        "box=(max(0,int(x*dpr)), max(0,int(y*dpr)), "
        "min(im.width,int((x+w)*dpr)), min(im.height,int((y+h)*dpr))); "
        "im.crop(box).save(dst)"
    )
    try:
        subprocess.run(
            [
                python,
                "-c",
                code,
                str(tmp_path),
                str(output_path),
                str(rect.x),
                str(rect.y),
                str(rect.width),
                str(rect.height),
                str(dpr),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        msg = f"Could not crop screenshot with {python!r}: {detail.strip()}"
        raise RuntimeError(msg) from exc
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    return output_path


def ocr_recaptcha_prompt(
    image_path: str | Path,
    *,
    tesseract_cmd: str = "tesseract",
) -> str:
    try:
        completed = subprocess.run(
            [tesseract_cmd, str(image_path), "stdout", "--psm", "6"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        msg = f"Could not OCR reCAPTCHA prompt with {tesseract_cmd!r}: {detail.strip()}"
        raise RuntimeError(msg) from exc
    return completed.stdout.strip()


RecaptchaChallengeType = Literal["dynamic", "skip", "one_shot", "unknown"]


def parse_recaptcha_challenge_type(prompt_text: str) -> RecaptchaChallengeType:
    """Classify Google's challenge instruction text.

    - ``dynamic``: clicked tiles refill with new images; keep clicking and
      "click verify once there are none left".
    - ``skip``: 4x4 one-shot grid; "if there are none, click skip".
    - ``one_shot``: plain "select all images" with a single verify.
    """

    lowered = re.sub(r"\s+", " ", prompt_text.strip().lower())
    if not lowered:
        return "unknown"
    if "none left" in lowered:
        return "dynamic"
    if "click skip" in lowered or "if there are none" in lowered:
        return "skip"
    if "select all" in lowered:
        return "one_shot"
    return "unknown"


async def read_recaptcha_challenge_prompt(
    page: object,
    challenge_rect: WidgetRect,
    *,
    prompt_image_path: str | Path,
    ml_python: str | None = None,
    tesseract_cmd: str = "tesseract",
) -> tuple[str, str | None]:
    """Crop and OCR the challenge prompt; returns (prompt_text, target_label)."""

    prompt_path = Path(prompt_image_path).expanduser().resolve()
    await crop_page_screenshot(
        page,
        recaptcha_prompt_rect(challenge_rect),
        prompt_path,
        ml_python=ml_python,
    )
    prompt_text = ocr_recaptcha_prompt(prompt_path, tesseract_cmd=tesseract_cmd)
    return prompt_text, parse_recaptcha_target_label(prompt_text)


async def wait_for_recaptcha_tiles_stable(
    page: object,
    grid: TileGrid,
    scratch_path: str | Path,
    *,
    ml_python: str | None = None,
    poll_secs: float = 0.7,
    timeout: float = 10.0,
) -> bool:
    """Wait until the challenge grid stops animating after a tile refill.

    Dynamic ("none left") challenges fade replacement images in over a few
    seconds; re-cropping mid-fade sees blank tiles and ends the round loop
    too early. Two identical consecutive crops mean the grid is stable.
    """

    scratch = Path(scratch_path)
    deadline = time.perf_counter() + timeout
    previous: bytes | None = None
    while time.perf_counter() < deadline:
        await asyncio.sleep(poll_secs)
        try:
            await crop_page_screenshot(page, grid, scratch, ml_python=ml_python)
            current = scratch.read_bytes()
        except (RuntimeError, OSError):
            continue
        if previous is not None and current == previous:
            return True
        previous = current
    return False


def parse_recaptcha_target_label(prompt_text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", prompt_text.strip().lower())
    normalized = normalized.replace("allimages", "all images")
    normalized = normalized.replace("allsquares", "all squares")
    patterns = (
        r"select all (?:images|squares) with ([a-z0-9 /-]+?)(?: if there| click|$)",
        r"select all ([a-z0-9 /-]+?)(?: if there| click|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        label = match.group(1).strip(" .:")
        label = re.sub(r"^(?:the|a|an) ", "", label)
        if label:
            return label
    return None


def plan_tile_clicks(
    image_path: str | Path,
    *,
    target_label: str,
    candidate_labels: tuple[str, ...],
    rows: int = 0,
    cols: int = 0,
    threshold: float = 0.5,
    model_id: str = "openai/clip-vit-base-patch32",
    device: str = "auto",
    cache_dir: str | Path = ".local/hf",
    local_files_only: bool = False,
) -> tuple[TileDecision, ...]:
    target_labels = label_variants(target_label)
    labels = tuple(dict.fromkeys((*candidate_labels, *target_labels)))
    if rows < 1 or cols < 1:
        rows, cols = infer_tile_grid_shape(image_path)
    resolved_model_id = model_id
    if local_files_only:
        resolved = resolve_local_hf_snapshot(model_id, cache_dir=cache_dir)
        if resolved is not None:
            resolved_model_id = str(resolved)
    classifier = HuggingFaceImageClassifier(
        ImageClassifierConfig(
            model_id=resolved_model_id,
            task="zero-shot-image-classification",
            device=device,
            cache_dir=Path(cache_dir),
            local_files_only=local_files_only,
            candidate_labels=labels,
        )
    )
    tile_dir = Path(cache_dir) / "recaptcha-tiles"
    tile_paths = split_image_grid(image_path, rows=rows, cols=cols, output_dir=tile_dir, prefix=Path(image_path).stem)
    decisions: list[TileDecision] = []
    for index, tile_path in enumerate(tile_paths):
        row, col = divmod(index, cols)
        best = best_label(classifier.classify(tile_path))
        if best is None:
            continue
        label, score = best
        if label not in target_labels or score < threshold:
            continue
        decisions.append(
            TileDecision(
                row=row,
                col=col,
                label=label,
                score=score,
                click_x=(col + 0.5) / cols,
                click_y=(row + 0.5) / rows,
            )
        )
    return tuple(decisions)


def plan_tile_clicks_ensemble(
    image_path: str | Path,
    *,
    target_label: str,
    candidate_labels: tuple[str, ...],
    model_ids: tuple[str, ...] = ("openai/clip-vit-base-patch32",),
    rows: int = 0,
    cols: int = 0,
    min_consensus: float = 1.0,
    min_target_score: float = 0.40,
    min_score_margin: float = 0.10,
    device: str = "auto",
    cache_dir: str | Path = ".local/hf",
    local_files_only: bool = False,
    active_tiles: tuple[tuple[int, int], ...] | None = None,
    augmentation_preset: TileAugmentationPreset = "none",
    hypothesis_templates: tuple[str, ...] = (),
    task: ImageClassificationTask = "zero-shot-image-classification",
) -> tuple[EnsembleTileDecision, ...]:
    if rows < 1 or cols < 1:
        rows, cols = infer_tile_grid_shape(image_path)
    all_votes: list[BinaryTileVote] = []
    for model_id in model_ids:
        all_votes.extend(
            score_tiles_binary(
                image_path,
                target_label=target_label,
                candidate_labels=candidate_labels,
                model_id=model_id,
                rows=rows,
                cols=cols,
                device=device,
                cache_dir=cache_dir,
                local_files_only=local_files_only,
                active_tiles=active_tiles,
                augmentation_preset=augmentation_preset,
                hypothesis_templates=hypothesis_templates,
                task=task,
            )
        )
    return aggregate_binary_tile_votes(
        tuple(all_votes),
        rows=rows,
        cols=cols,
        min_consensus=min_consensus,
        min_target_score=min_target_score,
        min_score_margin=min_score_margin,
    )


def score_tiles_binary(
    image_path: str | Path,
    *,
    target_label: str,
    candidate_labels: tuple[str, ...],
    model_id: str,
    rows: int,
    cols: int,
    device: str = "auto",
    cache_dir: str | Path = ".local/hf",
    local_files_only: bool = False,
    active_tiles: tuple[tuple[int, int], ...] | None = None,
    augmentation_preset: TileAugmentationPreset = "none",
    hypothesis_templates: tuple[str, ...] = (),
    task: ImageClassificationTask = "zero-shot-image-classification",
) -> tuple[BinaryTileVote, ...]:
    target_labels = label_variants(target_label)
    resolved_model_id = model_id
    if local_files_only:
        resolved = resolve_local_hf_snapshot(model_id, cache_dir=cache_dir)
        if resolved is not None:
            resolved_model_id = str(resolved)

    if task == "image-classification":
        # Supervised reCAPTCHA classifiers (e.g. verytuffcat/recaptcha) carry a
        # fixed label head; candidate_labels and templates do not apply. The
        # contrastive vote falls out of the model's own distribution: target =
        # the target class score, non-target = the best competing class.
        config = ImageClassifierConfig(
            model_id=resolved_model_id,
            task="image-classification",
            device=device,
            cache_dir=Path(cache_dir),
            local_files_only=local_files_only,
        )
    else:
        non_target_labels = tuple(
            label
            for label in dict.fromkeys(candidate_labels)
            if label not in target_labels
        )
        if not non_target_labels:
            non_target_labels = ("not " + target_label,)
        labels = (*target_labels, *non_target_labels)
        config = ImageClassifierConfig(
            model_id=resolved_model_id,
            task="zero-shot-image-classification",
            device=device,
            cache_dir=Path(cache_dir),
            local_files_only=local_files_only,
            candidate_labels=labels,
            hypothesis_templates=hypothesis_templates,
        )

    classifier = get_tile_classifier(config)
    tile_dir = Path(cache_dir) / "recaptcha-tiles"
    tile_paths = split_image_grid(image_path, rows=rows, cols=cols, output_dir=tile_dir, prefix=Path(image_path).stem)
    votes: list[BinaryTileVote] = []
    active_set = set(active_tiles) if active_tiles is not None else None
    for index, tile_path in enumerate(tile_paths):
        row, col = divmod(index, cols)
        if active_set is not None and (row, col) not in active_set:
            continue
        for augmentation_id, augmented_tile_path in iter_tile_augmentations(
            tile_path,
            preset=augmentation_preset,
            output_dir=tile_dir / "augmented",
        ):
            scores = {
                str(result.get("label", "")): float(result.get("score", 0.0))
                for result in classifier.classify(augmented_tile_path)
            }
            vote = binary_vote_from_scores(
                scores,
                row=row,
                col=col,
                model_id=model_id,
                target_labels=target_labels,
                augmentation_id=augmentation_id,
                source_tile_path=str(tile_path),
                augmented_tile_path=str(augmented_tile_path),
            )
            votes.append(vote)
    return tuple(votes)


def binary_vote_from_scores(
    scores: dict[str, float],
    *,
    row: int,
    col: int,
    model_id: str,
    target_labels: tuple[str, ...],
    augmentation_id: str,
    source_tile_path: str,
    augmented_tile_path: str,
) -> BinaryTileVote:
    """Reduce a label->score distribution to one contrastive target vote.

    Works for both zero-shot (scores over a curated candidate set) and
    supervised (scores over a fixed model head): target = best score among the
    target label's variants, non-target = best score among everything else.
    """

    target_set = set(target_labels)
    target_best = max(
        ((label, scores.get(label, 0.0)) for label in target_labels),
        key=lambda item: item[1],
    )
    non_target_items = [(label, score) for label, score in scores.items() if label not in target_set]
    non_target_best = (
        max(non_target_items, key=lambda item: item[1])
        if non_target_items
        else ("not " + target_labels[0], 0.0)
    )
    return BinaryTileVote(
        row=row,
        col=col,
        model_id=model_id,
        target_label=target_best[0],
        target_score=target_best[1],
        non_target_label=non_target_best[0],
        non_target_score=non_target_best[1],
        augmentation_id=augmentation_id,
        source_tile_path=source_tile_path,
        augmented_tile_path=augmented_tile_path,
    )


def iter_tile_augmentations(
    tile_path: str | Path,
    *,
    preset: TileAugmentationPreset = "none",
    output_dir: str | Path,
) -> tuple[tuple[str, Path], ...]:
    """Return tile image variants for test-time tile scoring."""

    source = Path(tile_path)
    if preset == "none":
        return (("identity", source),)
    if preset not in {"helpful", "denoise"}:
        msg = "augmentation_preset must be 'none', 'helpful', or 'denoise'"
        raise ValueError(msg)

    try:
        from PIL import Image, ImageEnhance, ImageFilter
    except ImportError as exc:
        msg = "Install the 'ml' extra to run reCAPTCHA tile augmentations."
        raise RuntimeError(msg) from exc

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    image = Image.open(source).convert("RGB")
    variants: list[tuple[str, Path]] = [("identity", source)]
    if preset == "denoise":
        transforms = (
            ("median_3", lambda value: value.filter(ImageFilter.MedianFilter(size=3))),
            ("median_5", lambda value: value.filter(ImageFilter.MedianFilter(size=5))),
        )
    else:
        transforms = (
            ("contrast_1_25", lambda value: ImageEnhance.Contrast(value).enhance(1.25)),
            ("sharpness_1_5", lambda value: ImageEnhance.Sharpness(value).enhance(1.5)),
            ("brightness_1_10", lambda value: ImageEnhance.Brightness(value).enhance(1.10)),
            ("center_crop_zoom_1_15", _center_crop_zoom_1_15),
        )
    for augmentation_id, transform in transforms:
        try:
            augmented = transform(image)
        except Exception:
            continue
        path = output / f"{source.stem}--{augmentation_id}{source.suffix}"
        augmented.save(path)
        variants.append((augmentation_id, path))
    return tuple(variants)


def _center_crop_zoom_1_15(image: object) -> object:
    width, height = image.size
    crop_width = max(1, int(width / 1.15))
    crop_height = max(1, int(height / 1.15))
    left = max(0, (width - crop_width) // 2)
    top = max(0, (height - crop_height) // 2)
    cropped = image.crop((left, top, left + crop_width, top + crop_height))
    return cropped.resize((width, height))


def aggregate_binary_tile_votes(
    votes: tuple[BinaryTileVote, ...],
    *,
    rows: int,
    cols: int,
    min_consensus: float = 1.0,
    min_target_score: float = 0.40,
    min_score_margin: float = 0.10,
) -> tuple[EnsembleTileDecision, ...]:
    decisions: list[EnsembleTileDecision] = []
    for row in range(rows):
        for col in range(cols):
            tile_votes = tuple(vote for vote in votes if vote.row == row and vote.col == col)
            if not tile_votes:
                continue
            target_votes = sum(
                1
                for vote in tile_votes
                if vote.votes_target
                and vote.target_score >= min_target_score
                and (vote.target_score - vote.non_target_score) >= min_score_margin
            )
            consensus = target_votes / len(tile_votes)
            if target_votes == 0 or consensus < min_consensus:
                continue
            decisions.append(
                EnsembleTileDecision(
                    row=row,
                    col=col,
                    target_votes=target_votes,
                    total_votes=len(tile_votes),
                    consensus=consensus,
                    click_x=(col + 0.5) / cols,
                    click_y=(row + 0.5) / rows,
                    votes=tile_votes,
                )
            )
    return tuple(decisions)


def resolve_local_hf_snapshot(model_id: str, *, cache_dir: str | Path | None = None) -> Path | None:
    if "/" not in model_id or Path(model_id).exists():
        path = Path(model_id)
        return path if path.exists() else None

    owner, name = model_id.split("/", 1)
    cache_name = f"models--{owner}--{name}"
    roots: list[Path] = []
    if cache_dir is not None:
        cache_path = Path(cache_dir).expanduser()
        roots.extend([cache_path, cache_path / "hub"])
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    for root in roots:
        model_dir = root / cache_name
        snapshots = model_dir / "snapshots"
        if not snapshots.exists():
            continue
        ref = model_dir / "refs" / "main"
        if ref.exists():
            snapshot = snapshots / ref.read_text(encoding="utf-8").strip()
            if (snapshot / "config.json").exists():
                return snapshot
        for snapshot in sorted(snapshots.iterdir(), reverse=True):
            if (snapshot / "config.json").exists():
                return snapshot
    return None


def label_variants(label: str) -> tuple[str, ...]:
    normalized = label.strip().lower()
    variants = [normalized]
    if normalized.endswith("ies"):
        variants.append(f"{normalized[:-3]}y")
    elif normalized.endswith("ses"):
        variants.append(normalized[:-2])
    elif normalized.endswith("s") and not normalized.endswith(("ss", "us")):
        variants.append(normalized[:-1])
    elif normalized.endswith(("s", "x", "ch", "sh")):
        variants.append(f"{normalized}es")
    else:
        variants.append(f"{normalized}s")
    return tuple(dict.fromkeys(variant for variant in variants if variant))


def plan_tile_clicks_with_python(
    image_path: str | Path,
    *,
    target_label: str,
    candidate_labels: tuple[str, ...],
    rows: int = 0,
    cols: int = 0,
    threshold: float = 0.5,
    model_id: str = "openai/clip-vit-base-patch32",
    device: str = "auto",
    cache_dir: str | Path = ".local/hf",
    local_files_only: bool = False,
    ml_python: str | None = None,
) -> tuple[TileDecision, ...]:
    python = ml_python or os.environ.get("OPENSESAME_ML_PYTHON") or "python"
    code = """
import json
import sys
from pathlib import Path
from open_sesame.harness.recaptcha_v2 import plan_tile_clicks

image_path = sys.argv[1]
target_label = sys.argv[2]
labels = tuple(json.loads(sys.argv[3]))
rows = int(sys.argv[4])
cols = int(sys.argv[5])
threshold = float(sys.argv[6])
model_id = sys.argv[7]
cache_dir = Path(sys.argv[8])
local_files_only = sys.argv[9] == "1"
device = sys.argv[10]
decisions = plan_tile_clicks(
    image_path,
    target_label=target_label,
    candidate_labels=labels,
    rows=rows,
    cols=cols,
    threshold=threshold,
    model_id=model_id,
    device=device,
    cache_dir=cache_dir,
    local_files_only=local_files_only,
)
print(json.dumps([decision.as_dict() for decision in decisions]))
"""
    try:
        completed = subprocess.run(
            [
                python,
                "-c",
                code,
                str(image_path),
                target_label,
                json.dumps(list(candidate_labels)),
                str(rows),
                str(cols),
                str(threshold),
                model_id,
                str(cache_dir),
                "1" if local_files_only else "0",
                device,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        msg = f"Could not classify tiles with {python!r}: {detail.strip()}"
        raise RuntimeError(msg) from exc
    payload = json.loads(completed.stdout)
    return tuple(
        TileDecision(
            row=int(item["row"]),
            col=int(item["col"]),
            label=str(item["label"]),
            score=float(item["score"]),
            click_x=float(item["click_x"]),
            click_y=float(item["click_y"]),
        )
        for item in payload
    )


def plan_tile_clicks_ensemble_with_python(
    image_path: str | Path,
    *,
    target_label: str,
    candidate_labels: tuple[str, ...],
    model_ids: tuple[str, ...],
    rows: int = 0,
    cols: int = 0,
    min_consensus: float = 1.0,
    min_target_score: float = 0.40,
    min_score_margin: float = 0.10,
    device: str = "auto",
    cache_dir: str | Path = ".local/hf",
    local_files_only: bool = False,
    ml_python: str | None = None,
    active_tiles: tuple[tuple[int, int], ...] | None = None,
    augmentation_preset: TileAugmentationPreset = "none",
    hypothesis_templates: tuple[str, ...] = (),
    task: ImageClassificationTask = "zero-shot-image-classification",
) -> tuple[EnsembleTileDecision, ...]:
    python = ml_python or os.environ.get("OPENSESAME_ML_PYTHON") or "python"
    code = """
import json
import sys
from pathlib import Path
from open_sesame.harness.recaptcha_v2 import plan_tile_clicks_ensemble

decisions = plan_tile_clicks_ensemble(
    sys.argv[1],
    target_label=sys.argv[2],
    candidate_labels=tuple(json.loads(sys.argv[3])),
    model_ids=tuple(json.loads(sys.argv[4])),
    rows=int(sys.argv[5]),
    cols=int(sys.argv[6]),
    min_consensus=float(sys.argv[7]),
    min_target_score=float(sys.argv[8]),
    min_score_margin=float(sys.argv[9]),
    device=sys.argv[10],
    cache_dir=Path(sys.argv[11]),
    local_files_only=sys.argv[12] == "1",
    active_tiles=tuple(tuple(item) for item in json.loads(sys.argv[13])) if sys.argv[13] else None,
    augmentation_preset=sys.argv[14],
    hypothesis_templates=tuple(json.loads(sys.argv[15])),
    task=sys.argv[16],
)
print(json.dumps([decision.as_dict() for decision in decisions]))
"""
    try:
        completed = subprocess.run(
            [
                python,
                "-c",
                code,
                str(image_path),
                target_label,
                json.dumps(list(candidate_labels)),
                json.dumps(list(model_ids)),
                str(rows),
                str(cols),
                str(min_consensus),
                str(min_target_score),
                str(min_score_margin),
                device,
                str(cache_dir),
                "1" if local_files_only else "0",
                json.dumps([list(item) for item in active_tiles]) if active_tiles is not None else "",
                augmentation_preset,
                json.dumps(list(hypothesis_templates)),
                task,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        msg = f"Could not run ensemble tile planner with {python!r}: {detail.strip()}"
        raise RuntimeError(msg) from exc
    return tuple(ensemble_decision_from_dict(item) for item in json.loads(completed.stdout))


def ensemble_decision_from_dict(item: dict[str, object]) -> EnsembleTileDecision:
    votes = tuple(
        BinaryTileVote(
            row=int(vote["row"]),
            col=int(vote["col"]),
            model_id=str(vote["model_id"]),
            target_label=str(vote["target_label"]),
            target_score=float(vote["target_score"]),
            non_target_label=str(vote["non_target_label"]),
            non_target_score=float(vote["non_target_score"]),
            augmentation_id=str(vote.get("augmentation_id", "identity")),
            source_tile_path=str(vote["source_tile_path"]) if vote.get("source_tile_path") is not None else None,
            augmented_tile_path=str(vote["augmented_tile_path"]) if vote.get("augmented_tile_path") is not None else None,
        )
        for vote in item["votes"]
        if isinstance(vote, dict)
    )
    return EnsembleTileDecision(
        row=int(item["row"]),
        col=int(item["col"]),
        target_votes=int(item["target_votes"]),
        total_votes=int(item["total_votes"]),
        consensus=float(item["consensus"]),
        click_x=float(item["click_x"]),
        click_y=float(item["click_y"]),
        votes=votes,
    )


async def _detect_captcha(page: object) -> str | None:
    detector = getattr(page, "detect_captcha", None)
    if detector is None:
        return None
    try:
        detected: Any = detector()
        if hasattr(detected, "__await__"):
            detected = await detected
    except Exception:
        return None
    return str(detected) if detected else None
