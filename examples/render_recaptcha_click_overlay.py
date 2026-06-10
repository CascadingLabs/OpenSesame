#!/usr/bin/env python3
"""Render clicked reCAPTCHA tiles from an audit metadata record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    record = json.loads(args.metadata.read_text())
    payload = record.get("payload", record)
    run_dir = args.metadata.parent
    output_dir = args.output_dir or run_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered = []
    for round_record in payload.get("rounds", []):
        path = resolve_round_image(round_record, record, run_dir)
        if path is None or not path.exists():
            continue
        output = output_dir / f"round_{round_record.get('round', len(rendered) + 1)}_click_overlay.png"
        render_round(path, round_record, output)
        rendered.append(output)

    if not rendered:
        raise SystemExit("No round images found to render")
    for path in rendered:
        print(path)


def resolve_round_image(round_record: dict[str, Any], record: dict[str, Any], run_dir: Path) -> Path | None:
    round_number = int(round_record.get("round", 0) or 0)
    artifacts = record.get("artifacts", {})
    if round_number:
        artifact = artifacts.get(f"round_{round_number}_challenge")
        if artifact:
            return Path(str(artifact))
    image_path = round_record.get("challenge_image_path")
    if not image_path:
        return None
    path = Path(str(image_path))
    if path.is_absolute():
        return path
    candidate = run_dir / path
    if candidate.exists():
        return candidate
    return path


def render_round(image_path: Path, round_record: dict[str, Any], output: Path) -> None:
    image = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    rows, cols = infer_rows_cols(round_record)
    draw_grid(draw, image.size, rows, cols)

    decisions = round_record.get("tile_plan") or [
        {
            "row": item["row"],
            "col": item["col"],
            "score": item.get("consensus", 0.0),
            "label": item.get("votes", [{}])[0].get("target_label", ""),
            "click_x": item["click_x"],
            "click_y": item["click_y"],
        }
        for item in round_record.get("ensemble_plan", [])
    ]
    for index, decision in enumerate(decisions, start=1):
        draw_decision(draw, image.size, rows, cols, decision, index)

    if not decisions:
        draw.text((12, 12), "no clicks planned", fill=(255, 64, 64, 255))

    composed = Image.alpha_composite(image, overlay).convert("RGB")
    composed.save(output)


def infer_rows_cols(round_record: dict[str, Any]) -> tuple[int, int]:
    states = round_record.get("tile_states") or []
    rows = max((int(state.get("row", 0)) for state in states), default=2) + 1
    cols = max((int(state.get("col", 0)) for state in states), default=2) + 1
    return rows, cols


def draw_grid(draw: ImageDraw.ImageDraw, size: tuple[int, int], rows: int, cols: int) -> None:
    width, height = size
    for col in range(1, cols):
        x = round(width * col / cols)
        draw.line((x, 0, x, height), fill=(255, 255, 0, 180), width=2)
    for row in range(1, rows):
        y = round(height * row / rows)
        draw.line((0, y, width, y), fill=(255, 255, 0, 180), width=2)


def draw_decision(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    rows: int,
    cols: int,
    decision: dict[str, Any],
    index: int,
) -> None:
    width, height = size
    row = int(decision["row"])
    col = int(decision["col"])
    left = round(width * col / cols)
    top = round(height * row / rows)
    right = round(width * (col + 1) / cols)
    bottom = round(height * (row + 1) / rows)
    cx = round(width * float(decision.get("click_x", (col + 0.5) / cols)))
    cy = round(height * float(decision.get("click_y", (row + 0.5) / rows)))

    draw.rectangle((left, top, right, bottom), outline=(255, 0, 0, 255), width=5)
    radius = max(12, min(width, height) // 28)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(255, 0, 0, 200), outline=(255, 255, 255, 255), width=2)

    label = str(index)
    score = decision.get("score")
    if score is not None:
        label = f"{label}: {float(score):.2f}"
    font = ImageFont.load_default()
    draw.text((left + 6, top + 6), label, fill=(255, 255, 255, 255), font=font)


if __name__ == "__main__":
    main()

