#!/usr/bin/env python3
"""Transcribe a downloaded reCAPTCHA audio challenge with a local ASR model."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from open_sesame.solvers.audio import extract_asr_text
from open_sesame.solvers.ml_config import resolve_torch_device_info


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path, help="Downloaded challenge audio, usually an MP3.")
    parser.add_argument("--model", default="openai/whisper-tiny.en")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--chunk-length-s", type=float, default=0.0)
    parser.add_argument("--stride-length-s", type=float, default=0.0)
    args = parser.parse_args()

    payload = dictate_audio(
        args.audio,
        model_id=args.model,
        device=args.device,
        cache_dir=args.cache_dir,
        local_files_only=args.local_files_only,
        chunk_length_s=args.chunk_length_s,
        stride_length_s=args.stride_length_s,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


def dictate_audio(
    audio_path: str | Path,
    *,
    model_id: str = "openai/whisper-tiny.en",
    device: str = "auto",
    cache_dir: str | Path | None = Path(".local/hf"),
    local_files_only: bool = False,
    chunk_length_s: float = 0.0,
    stride_length_s: float = 0.0,
) -> dict[str, Any]:
    path = Path(audio_path).expanduser().resolve()
    if not path.exists():
        msg = f"audio file not found: {path}"
        raise FileNotFoundError(msg)
    prepare_cache_env(cache_dir)

    try:
        from transformers import pipeline
    except ImportError as exc:
        msg = "Install the 'ml-audio' extra to run local audio dictation."
        raise RuntimeError(msg) from exc

    device_info = resolve_torch_device_info(device)
    pipe_kwargs: dict[str, Any] = {
        "task": "automatic-speech-recognition",
        "model": model_id,
        "device": device_info.pipeline_device,
    }
    if cache_dir is not None:
        pipe_kwargs["model_kwargs"] = {"cache_dir": str(Path(cache_dir).expanduser())}
    if local_files_only:
        pipe_kwargs["model_kwargs"] = {
            **dict(pipe_kwargs.get("model_kwargs") or {}),
            "local_files_only": True,
        }

    try:
        recognizer = pipeline(**pipe_kwargs)
    except Exception as exc:
        msg = f"Could not load ASR model {model_id!r}: {exc}"
        raise RuntimeError(msg) from exc

    generate_kwargs = {}
    call_kwargs: dict[str, Any] = {}
    if chunk_length_s > 0:
        call_kwargs["chunk_length_s"] = chunk_length_s
    if stride_length_s > 0:
        call_kwargs["stride_length_s"] = stride_length_s

    try:
        raw = recognizer(path.read_bytes(), generate_kwargs=generate_kwargs, **call_kwargs)
    except Exception as exc:
        msg = (
            f"Could not transcribe {path}: {exc}. "
            "Confirm ffmpeg is installed and the audio file is readable."
        )
        raise RuntimeError(msg) from exc

    return {
        "audio_path": str(path),
        "audio_bytes": path.stat().st_size,
        "model_id": model_id,
        "device_info": device_info.as_dict(),
        "text": extract_asr_text(raw),
        "raw_prediction": raw,
    }


def prepare_cache_env(cache_dir: str | Path | None) -> None:
    if cache_dir is None:
        return
    root = Path(cache_dir).expanduser()
    modules_dir = root / "modules"
    root.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(root))
    os.environ.setdefault("HF_HUB_CACHE", str(root / "hub"))
    os.environ.setdefault("HF_MODULES_CACHE", str(modules_dir))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(root / "transformers"))


if __name__ == "__main__":
    main()
