#!/usr/bin/env python3
"""Drive a Google Search reCAPTCHA v2 wall with VoidCrawl mouse actions."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from open_sesame.harness.google_search import build_google_search_url
from open_sesame.harness.page_sync import SerializedPage
from open_sesame.solvers.image_classification import DEFAULT_HYPOTHESIS_TEMPLATES
from open_sesame.harness.recaptcha_v2 import (
    attempt_recaptcha_audio_challenge,
    attempt_recaptcha_v2_checkbox,
    build_recaptcha_research_report,
    click_recaptcha_verify_button,
    click_tile_decisions,
    infer_tile_grid_shape_with_python,
    label_variants,
    parse_recaptcha_challenge_type,
    parse_widget_rect,
    persist_recaptcha_attempt,
    plan_tile_clicks_ensemble_with_python,
    read_recaptcha_challenge_prompt,
    recaptcha_challenge_rect_js,
    recaptcha_tile_grid_rect,
    collect_recaptcha_page_metadata,
    crop_page_screenshot,
    inspect_tile_visual_states_with_python,
    wait_for_recaptcha_result,
    wait_for_recaptcha_tiles_stable,
)


class ScreenshotVideoRecorder:
    def __init__(
        self,
        page: object,
        *,
        video_path: Path | None,
        frame_dir: Path | None,
        interval: float,
    ) -> None:
        self.page = page
        self.video_path = video_path
        self.frame_dir = frame_dir
        self.interval = max(0.2, interval)
        self.frames: list[Path] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self.video_path is None and self.frame_dir is None:
            return
        if self.frame_dir is None:
            assert self.video_path is not None
            self.frame_dir = self.video_path.with_suffix("") / "frames"
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._capture_loop())

    async def stop(self) -> dict[str, object]:
        if self._task is None:
            return {}
        # Cancelling an in-flight CDP call permanently loses a VoidCrawl
        # page, so ask the loop to exit and only cancel as a last resort.
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        encoded = self._encode_video()
        return {
            "debug_frame_dir": str(self.frame_dir) if self.frame_dir else None,
            "debug_frame_count": len(self.frames),
            "debug_video_path": str(encoded) if encoded else None,
        }

    async def _capture_loop(self) -> None:
        assert self.frame_dir is not None
        index = 0
        while not self._stop.is_set():
            frame = self.frame_dir / f"frame_{index:06d}.png"
            try:
                png = await self.page.screenshot_png()
                frame.write_bytes(png)
                self.frames.append(frame)
                index += 1
            except Exception:
                pass
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)

    def _encode_video(self) -> Path | None:
        if self.video_path is None or not self.frames or shutil.which("ffmpeg") is None:
            return None
        self.video_path.parent.mkdir(parents=True, exist_ok=True)
        pattern = str(self.frame_dir / "frame_%06d.png") if self.frame_dir else ""
        command = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(max(1, round(1 / self.interval))),
            "-i",
            pattern,
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-pix_fmt",
            "yuv420p",
            str(self.video_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        return self.video_path if completed.returncode == 0 and self.video_path.exists() else None


async def run(args: argparse.Namespace) -> dict[str, object]:
    from voidcrawl import BrowserConfig, BrowserSession

    phases: list[dict[str, object]] = []
    if args.ws_url:
        # Attach to Chrome already running in a headful container (Sway virtual
        # display + noVNC). The container owns launch flags, profile, and the
        # display, so we only hand over the CDP endpoint — no jitter on the host
        # compositor, and the run is watchable over noVNC.
        config_kwargs = {"ws_url": args.ws_url}
    else:
        config_kwargs = {
            "headless": not args.headful,
            "stealth": True,
            "chrome_executable": args.chrome_executable,
            "extra_args": ["--window-size=1365,900"],
            # Token minting is gated by reCAPTCHA's per-session risk score (IP +
            # fingerprint + cookie history), not tile correctness. A residential
            # proxy and a warmed persistent profile are the levers that move it.
            "proxy": args.proxy,
            "user_data_dir": args.user_data_dir,
        }
    config_kwargs = {key: value for key, value in config_kwargs.items() if value is not None}
    target = args.url or build_google_search_url(args.query, hl=args.hl, gl=args.gl)
    async with BrowserSession(BrowserConfig(**config_kwargs)) as browser:
        phases.append({"phase": "browser_started", "target_url": target})
        page = SerializedPage(await browser.new_page("about:blank"))
        phases.append({"phase": "page_created"})
        # The CDP screenshot recorder competes with every other page op through
        # the SerializedPage lock; under load (mouse moves + crops) that can
        # overflow VoidCrawl's request timeout. Disable it when watching the run
        # over VNC/noVNC, where an external screen recorder is better anyway.
        recorder = ScreenshotVideoRecorder(
            page,
            video_path=None if args.no_debug_video else args.debug_video,
            frame_dir=None if args.no_debug_video else args.debug_frame_dir,
            interval=args.debug_frame_interval,
        )
        await recorder.start()
        payload: dict[str, object] | None = None
        try:
            response = await page.goto(target, timeout=args.timeout, capture_endpoints=True)
            phases.append(
                {
                    "phase": "target_loaded",
                    "url": str(await page.url() or target),
                    "status_code": response.status_code,
                    "endpoints": response.endpoints,
                    "endpoints_truncated": response.endpoints_truncated,
                    "endpoint_sanitizer_version": response.endpoint_sanitizer_version,
                }
            )
            try:
                await page.wait_for_network_idle(timeout=args.timeout)
                phases.append({"phase": "network_idle"})
            except Exception:
                phases.append({"phase": "network_idle_timeout", "timeout": args.timeout})

            # Human cadence: dwell on the loaded page before touching the widget
            # so the checkbox interaction is not suspiciously instantaneous.
            if args.pre_checkbox_wait > 0:
                await asyncio.sleep(args.pre_checkbox_wait)
                phases.append({"phase": "pre_checkbox_dwell", "seconds": args.pre_checkbox_wait})

            state = await attempt_recaptcha_v2_checkbox(
                page,
                wait_secs=args.wait_secs,
                screenshot_path=args.screenshot,
                challenge_image_path=args.challenge_image if args.challenge_mode == "image" else None,
                prompt_image_path=args.prompt_image if args.challenge_mode == "image" else None,
                ml_python=args.ml_python,
                tesseract_cmd=args.tesseract_cmd,
                seed=args.seed,
            )
            payload = {
                "query": args.query,
                "target_url": target,
                "phases": phases,
                "state": state.as_dict(),
                "page_metadata_before_tiles": await collect_recaptcha_page_metadata(page),
            }
            target_label = state.target_label if args.target_label == "auto" else args.target_label
            if args.challenge_mode == "audio":
                await run_audio_mode(page, args, payload, phases, state)
            elif target_label and state.challenge_image_path and state.challenge_rect:
                await run_image_mode(page, args, payload, phases, state, target_label)
            payload["research_report"] = build_recaptcha_research_report(payload).as_dict()
            if args.audit_dir:
                payload["audit_record_path"] = str(persist_recaptcha_attempt(payload, audit_dir=args.audit_dir))
                payload["research_report"] = build_recaptcha_research_report(payload).as_dict()
            return payload
        finally:
            video_artifacts = await recorder.stop()
            phases.append({"phase": "debug_video_stopped", **video_artifacts})
            if payload is not None:
                payload["debug_video"] = video_artifacts


async def run_audio_mode(
    page: object,
    args: argparse.Namespace,
    payload: dict[str, object],
    phases: list[dict[str, object]],
    state: Any,
) -> None:
    if state.challenge_rect is None:
        phases.append({"phase": "audio_mode_skipped", "reason": "challenge_rect_missing"})
        return
    audio = await attempt_recaptcha_audio_challenge(
        page,
        state.challenge_rect,
        screenshot_path=args.audio_screenshot,
        download_dir=args.audio_download_dir,
        download_timeout=args.audio_download_timeout,
        seed=args.seed + 300,
        tesseract_cmd=args.tesseract_cmd,
    )
    payload["audio_challenge"] = audio.as_dict()
    if args.post_verify_wait > 0:
        await asyncio.sleep(args.post_verify_wait)
    await collect_post_verify(page, args, payload, phases)


async def run_image_mode(
    page: object,
    args: argparse.Namespace,
    payload: dict[str, object],
    phases: list[dict[str, object]],
    state: Any,
    target_label: str,
) -> None:
    base_labels = tuple(label.strip() for label in args.labels.split(",") if label.strip())
    model_ids = tuple(model.strip() for model in args.models.split(",") if model.strip())
    hypothesis_templates = (
        DEFAULT_HYPOTHESIS_TEMPLATES if args.prompt_ensemble else ()
    )
    rounds: list[dict[str, object]] = []
    challenges: list[dict[str, object]] = []
    all_tile_traces = []
    verify_trace = None
    grid = None
    round_counter = 0

    challenge_rect = state.challenge_rect
    prompt_text = state.prompt_text or ""
    prompt_image_current = state.prompt_image_path
    first_image = Path(state.challenge_image_path) if state.challenge_image_path else None

    for challenge_index in range(1, args.max_challenges + 1):
        challenge_type = parse_recaptcha_challenge_type(prompt_text)
        labels = base_labels
        for variant in label_variants(target_label):
            if variant not in labels:
                labels = (*labels, variant)

        if first_image is None:
            first_image = args.challenge_image.with_name(
                f"{args.challenge_image.stem}-c{challenge_index}{args.challenge_image.suffix}"
            )
            await crop_page_screenshot(
                page,
                recaptcha_tile_grid_rect(challenge_rect),
                first_image,
                ml_python=args.ml_python,
            )
        rows, cols = (args.rows, args.cols)
        if rows < 1 or cols < 1:
            rows, cols = infer_tile_grid_shape_with_python(first_image, ml_python=args.ml_python)
        grid = recaptcha_tile_grid_rect(challenge_rect, rows=rows, cols=cols)

        challenge_record: dict[str, object] = {
            "challenge": challenge_index,
            "challenge_type": challenge_type,
            "prompt_text": prompt_text,
            "prompt_image_path": str(prompt_image_current) if prompt_image_current else None,
            "target_label": target_label,
            "rows": rows,
            "cols": cols,
        }
        for round_index in range(args.max_rounds):
            round_counter += 1
            if round_index == 0:
                round_image = first_image
            else:
                round_image = args.challenge_image.with_name(
                    f"{args.challenge_image.stem}-c{challenge_index}-r{round_index + 1}{args.challenge_image.suffix}"
                )
                await crop_page_screenshot(page, grid, round_image, ml_python=args.ml_python)
            tile_states = inspect_tile_visual_states_with_python(
                round_image,
                rows=rows,
                cols=cols,
                ml_python=args.ml_python,
            )
            active_tiles = tuple((state.row, state.col) for state in tile_states if state.active)
            ensemble_decisions = plan_tile_clicks_ensemble_with_python(
                round_image,
                target_label=target_label,
                candidate_labels=labels,
                model_ids=model_ids,
                rows=rows,
                cols=cols,
                min_consensus=args.min_consensus,
                min_target_score=args.min_target_score,
                min_score_margin=args.min_score_margin,
                device=args.device,
                cache_dir=args.cache_dir,
                local_files_only=args.local_files_only,
                ml_python=args.ml_python,
                active_tiles=active_tiles,
                augmentation_preset=args.augmentations,
                hypothesis_templates=hypothesis_templates,
                task=args.classifier_task,
            )
            decisions = tuple(decision.as_tile_decision(label=target_label) for decision in ensemble_decisions)
            tile_traces = await click_tile_decisions(page, grid, decisions, seed=args.seed + 100 + round_counter)
            all_tile_traces.extend(tile_traces)
            rounds.append(
                {
                    "round": round_counter,
                    "challenge": challenge_index,
                    "challenge_type": challenge_type,
                    "target_label": target_label,
                    "challenge_image_path": str(round_image),
                    "device": args.device,
                    "augmentations": args.augmentations,
                    "tile_states": [state.as_dict() for state in tile_states],
                    "ensemble_plan": [decision.as_dict() for decision in ensemble_decisions],
                    "tile_plan": [decision.as_dict() for decision in decisions],
                    "tile_click_traces": [trace.as_dict() for trace in tile_traces],
                }
            )
            if not decisions:
                break
            if challenge_type == "dynamic":
                stable = await wait_for_recaptcha_tiles_stable(
                    page,
                    grid,
                    args.challenge_image.with_name(f"{args.challenge_image.stem}-stability{args.challenge_image.suffix}"),
                    ml_python=args.ml_python,
                    timeout=args.refill_timeout,
                )
                rounds[-1]["refill_stable"] = stable
            elif args.round_wait > 0:
                await asyncio.sleep(args.round_wait)

        verify_trace = await click_recaptcha_verify_button(page, challenge_rect, seed=args.seed + 200 + challenge_index)
        token, solved = await wait_for_recaptcha_result(page, wait_secs=max(args.post_verify_wait, 1.0))
        challenge_record["rounds_played"] = round_index + 1
        challenge_record["token_present"] = bool(token)
        challenge_record["solved"] = solved
        challenges.append(challenge_record)
        phases.append(
            {
                "phase": "challenge_verified",
                "challenge": challenge_index,
                "challenge_type": challenge_type,
                "target_label": target_label,
                "token_present": bool(token),
            }
        )
        if token or solved:
            break

        # No token and the widget is still up: Google chained a new challenge
        # (or rejected this one). Re-read the frame, prompt, and grid shape.
        challenge_rect = parse_widget_rect(await page.eval_js(recaptcha_challenge_rect_js()))
        if challenge_rect is None:
            break
        prompt_image = args.prompt_image.with_name(
            f"{args.prompt_image.stem}-c{challenge_index + 1}{args.prompt_image.suffix}"
        )
        try:
            prompt_text, next_label = await read_recaptcha_challenge_prompt(
                page,
                challenge_rect,
                prompt_image_path=prompt_image,
                ml_python=args.ml_python,
                tesseract_cmd=args.tesseract_cmd,
            )
        except RuntimeError as exc:
            phases.append({"phase": "challenge_prompt_ocr_failed", "error": str(exc)})
            break
        if not next_label:
            phases.append({"phase": "challenge_prompt_no_target", "prompt_text": prompt_text})
            break
        target_label = next_label
        prompt_image_current = str(prompt_image)
        first_image = None

    if grid is not None:
        payload["tile_grid"] = grid.as_dict()
    payload["rounds"] = rounds
    payload["challenges"] = challenges
    payload["tile_click_traces"] = [trace.as_dict() for trace in all_tile_traces]
    if verify_trace is not None:
        payload["verify_click_trace"] = verify_trace.as_dict()
    await collect_post_verify(page, args, payload, phases)


async def collect_post_verify(
    page: object,
    args: argparse.Namespace,
    payload: dict[str, object],
    phases: list[dict[str, object]],
) -> None:
    token = str(await page.eval_js("document.querySelector('#g-recaptcha-response, textarea[name=\"g-recaptcha-response\"]')?.value || ''") or "")
    try:
        post_captcha = await page.detect_captcha()
    except Exception as exc:
        post_captcha = None
        phases.append({"phase": "post_detect_captcha_failed", "error": f"{type(exc).__name__}: {exc}"})
    post_screenshot = None
    if args.post_verify_screenshot:
        post_screenshot = str(args.post_verify_screenshot.expanduser().resolve())
        args.post_verify_screenshot.parent.mkdir(parents=True, exist_ok=True)
        args.post_verify_screenshot.write_bytes(await page.screenshot_png())
    payload["post_verify"] = {
        "token_present": bool(token),
        "token_length": len(token),
        "captcha_kind": post_captcha,
        "screenshot_path": post_screenshot,
    }
    payload["page_metadata_after_verify"] = await collect_recaptcha_page_metadata(page)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="weather new york")
    parser.add_argument(
        "--url",
        help="Direct page URL to inspect instead of building a Google Search URL.",
    )
    parser.add_argument("--headful", action="store_true")
    parser.add_argument(
        "--ws-url",
        help="Attach to an existing Chrome over CDP (e.g. http://localhost:19222 "
        "for the VoidCrawl headful container) instead of launching a browser.",
    )
    parser.add_argument("--chrome-executable")
    parser.add_argument(
        "--proxy",
        help="Proxy URL (use a residential/mobile proxy to lift reCAPTCHA risk score).",
    )
    parser.add_argument(
        "--user-data-dir",
        help="Persistent Chrome profile dir; reuse a warmed profile with cookie history.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--run-timeout",
        type=float,
        default=90.0,
        help="Hard timeout for the whole actor run; emits JSON on timeout.",
    )
    parser.add_argument("--wait-secs", type=float, default=20.0)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--hl", default="en")
    parser.add_argument("--gl", default="us")
    parser.add_argument("--screenshot", type=Path, default=Path(".local/recaptcha/google-recaptcha.png"))
    parser.add_argument("--challenge-image", type=Path, default=Path(".local/recaptcha/google-recaptcha-challenge.png"))
    parser.add_argument("--prompt-image", type=Path, default=Path(".local/recaptcha/google-recaptcha-prompt.png"))
    parser.add_argument(
        "--challenge-mode",
        choices=["image", "audio"],
        default="image",
        help="Challenge interaction path after the checkbox opens a challenge.",
    )
    parser.add_argument(
        "--audio-screenshot",
        type=Path,
        default=Path(".local/recaptcha/google-recaptcha-audio.png"),
    )
    parser.add_argument(
        "--audio-download-dir",
        type=Path,
        default=Path(".local/recaptcha/audio-downloads"),
    )
    parser.add_argument("--audio-download-timeout", type=float, default=20.0)
    parser.add_argument(
        "--debug-video",
        type=Path,
        default=Path(".local/recaptcha/google-recaptcha-debug.mp4"),
        help="Record sampled page screenshots to an MP4 for later review.",
    )
    parser.add_argument(
        "--debug-frame-dir",
        type=Path,
        default=None,
        help="Directory for raw debug video frames; defaults next to --debug-video.",
    )
    parser.add_argument("--debug-frame-interval", type=float, default=0.5)
    parser.add_argument(
        "--no-debug-video",
        action="store_true",
        help="Disable the CDP screenshot recorder (watch over VNC instead; avoids contention).",
    )
    parser.add_argument(
        "--target-label",
        default="auto",
        help="Local CPU tile target, e.g. bus. Use 'auto' to OCR Google's prompt.",
    )
    parser.add_argument("--labels", default="bus,crosswalk,traffic light,car,bicycle,motorcycle,stairs,chimney")
    parser.add_argument("--rows", type=int, default=0, help="Grid rows; 0 infers from the crop.")
    parser.add_argument("--cols", type=int, default=0, help="Grid columns; 0 infers from the crop.")
    parser.add_argument("--models", default="openai/clip-vit-base-patch32")
    parser.add_argument(
        "--classifier-task",
        choices=["zero-shot-image-classification", "image-classification"],
        default="zero-shot-image-classification",
        help=(
            "Use 'image-classification' with a supervised reCAPTCHA model "
            "(e.g. --models verytuffcat/recaptcha) for far higher recall on "
            "noised tiles; candidate labels/templates are then ignored."
        ),
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--augmentations", choices=["none", "helpful", "denoise"], default="none")
    parser.add_argument(
        "--prompt-ensemble",
        action="store_true",
        help="Average CLIP scores across a bank of prompt templates (lifts weak true tiles).",
    )
    parser.add_argument("--min-consensus", type=float, default=1.0)
    parser.add_argument("--min-target-score", type=float, default=0.40)
    parser.add_argument("--min-score-margin", type=float, default=0.10)
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--audit-dir", type=Path, default=Path(".local/recaptcha-runs"))
    parser.add_argument("--post-verify-wait", type=float, default=3.0)
    parser.add_argument(
        "--pre-checkbox-wait",
        type=float,
        default=0.0,
        help="Seconds to dwell after load before clicking the checkbox (human cadence).",
    )
    parser.add_argument("--round-wait", type=float, default=1.25)
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument(
        "--max-challenges",
        type=int,
        default=3,
        help="How many chained challenges to attempt before giving up.",
    )
    parser.add_argument(
        "--refill-timeout",
        type=float,
        default=10.0,
        help="Max seconds to wait for dynamic tile refills to stop animating.",
    )
    parser.add_argument(
        "--post-verify-screenshot",
        type=Path,
        default=Path(".local/recaptcha/google-recaptcha-post-verify.png"),
    )
    parser.add_argument(
        "--ml-python",
        default="python",
        help="Python executable with local CPU image dependencies; defaults to system python.",
    )
    parser.add_argument("--tesseract-cmd", default="tesseract")
    args = parser.parse_args()

    try:
        payload = asyncio.run(asyncio.wait_for(run(args), timeout=args.run_timeout))
    except TimeoutError:
        target = args.url or build_google_search_url(args.query, hl=args.hl, gl=args.gl)
        payload = {
            "query": args.query,
            "target_url": target,
            "ok": False,
            "error": f"run timed out after {args.run_timeout:.1f}s",
            "debug_video_path": str(args.debug_video) if args.debug_video else None,
            "debug_frame_dir": (
                str(args.debug_frame_dir)
                if args.debug_frame_dir
                else str(args.debug_video.with_suffix("") / "frames")
                if args.debug_video
                else None
            ),
        }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
