#!/usr/bin/env python3
"""DOM-driven reCAPTCHA solve for Google's same-origin api2/demo.

The challenge bframe on ``api2/demo`` is same-origin, so we read instructions,
grid shape, and exact tile rects from the DOM (no OCR, no geometry estimate),
crop each grid precisely, classify with a local model, click target tiles at
their true centers, and read the minted token from the parent textarea.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from open_sesame.harness.page_sync import SerializedPage, wait_page_op
from open_sesame.harness.recaptcha_dom import DomChallengeState, read_dom_challenge_state
from open_sesame.harness.recaptcha_v2 import (
    WidgetRect,
    attempt_recaptcha_v2_checkbox,
    click_like_human,
    crop_page_screenshot,
    label_variants,
    parse_recaptcha_challenge_type,
    parse_recaptcha_target_label,
    plan_tile_clicks_ensemble,
)


_START = time.perf_counter()


def log(msg: str) -> None:
    print(f"[t={time.perf_counter() - _START:5.1f}s] {msg}", file=sys.stderr, flush=True)


async def timed(label: str, coro: Any, *, timeout: float) -> Any:
    """Await an op with a hard timeout and log its duration."""
    t = time.perf_counter()
    try:
        result = await wait_page_op(coro, timeout=timeout)
        log(f"{label}: {time.perf_counter() - t:.1f}s")
        return result
    except TimeoutError:
        log(f"{label}: TIMEOUT after {timeout:.1f}s")
        raise


async def solve(args: argparse.Namespace) -> dict[str, Any]:
    from voidcrawl import BrowserConfig, BrowserSession

    if args.ws_url:
        config = BrowserConfig(ws_url=args.ws_url)
    else:
        config = BrowserConfig(headless=not args.headful, stealth=True, extra_args=["--window-size=1365,900"])

    out = Path(args.work_dir)
    out.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"url": args.url, "challenges": []}

    async with BrowserSession(config) as browser:
        page = SerializedPage(await browser.new_page("about:blank"))
        log("navigating")
        await timed("goto", page.goto(args.url, timeout=args.timeout), timeout=args.timeout + 5)
        try:
            await wait_page_op(page.wait_for_network_idle(timeout=args.timeout), timeout=args.timeout + 2)
        except Exception:
            pass
        await asyncio.sleep(args.pre_checkbox_wait)

        state = await timed("checkbox", attempt_recaptcha_v2_checkbox(page, wait_secs=args.wait_secs), timeout=args.wait_secs + 10)
        record["checkbox_signals"] = list(state.signals)
        log(f"checkbox signals: {state.signals}")
        token = await read_token(page)
        if token:
            record["token_present"] = True
            record["token_length"] = len(token)
            record["outcome"] = "passed-on-checkbox"
            return record

        for challenge_index in range(1, args.max_challenges + 1):
            log(f"--- challenge {challenge_index}: waiting for grid")
            dom = await wait_for_dom_grid(page, timeout=args.challenge_timeout)
            log(f"challenge {challenge_index}: grid present={dom.present} {dom.rows}x{dom.cols} target='{dom.instructions[:40]}'")
            if not dom.ok:
                record["challenges"].append({"challenge": challenge_index, "dom_error": dom.reason})
                break
            if not dom.present:
                # No grid (e.g. verifying / passed). Re-check token.
                token = await read_token(page)
                if token:
                    break
                record["challenges"].append({"challenge": challenge_index, "note": "no-grid"})
                break

            challenge_record = await solve_one_challenge(page, dom, args, challenge_index, out)
            record["challenges"].append(challenge_record)

            await timed("verify-click", click_verify(page, dom, args), timeout=10)
            token = await read_token_after(page, args.post_verify_wait)
            log(f"challenge {challenge_index}: token_after={bool(token)}")
            challenge_record["token_after"] = bool(token)
            if token:
                break

        token = await read_token(page)
        record["token_present"] = bool(token)
        record["token_length"] = len(token)
        record["outcome"] = "passed" if token else "no-token"
        if token:
            record["token_preview"] = token[:60] + "..."
        return record


async def solve_one_challenge(
    page: object,
    dom: DomChallengeState,
    args: argparse.Namespace,
    challenge_index: int,
    out: Path,
) -> dict[str, Any]:
    target = parse_recaptcha_target_label(dom.instructions)
    challenge_type = parse_recaptcha_challenge_type(dom.instructions)
    rec: dict[str, Any] = {
        "challenge": challenge_index,
        "instructions": dom.instructions,
        "target": target,
        "type": challenge_type,
        "grid": f"{dom.rows}x{dom.cols}",
        "rounds": [],
    }
    if not target:
        rec["note"] = "no-target-parsed"
        return rec

    labels = tuple(args.labels.split(","))
    for variant in label_variants(target):
        if variant not in labels:
            labels = (*labels, variant)

    for round_index in range(1, args.max_rounds + 1):
        dom = await timed(f"  r{round_index} read-dom", read_dom_challenge_state(page), timeout=8)
        if not dom.present or not dom.tiles:
            break
        grid_rect = dom.grid_rect
        if grid_rect is None:
            break
        gx, gy, gw, gh = grid_rect
        grid_image = out / f"c{challenge_index}-r{round_index}.png"
        await timed(
            f"  r{round_index} crop",
            crop_page_screenshot(page, WidgetRect(x=gx, y=gy, width=gw, height=gh), grid_image, ml_python=args.ml_python),
            timeout=15,
        )

        # In-process classification: the singleton classifier cache keeps the
        # model resident across rounds (no subprocess reload between rounds).
        t = time.perf_counter()
        decisions = await asyncio.to_thread(
            plan_tile_clicks_ensemble,
            grid_image,
            target_label=target,
            candidate_labels=labels,
            model_ids=tuple(args.models.split(",")),
            rows=dom.rows,
            cols=dom.cols,
            min_consensus=args.min_consensus,
            min_target_score=args.min_target_score,
            min_score_margin=args.min_score_margin,
            device=args.device,
            cache_dir=args.cache_dir,
            task=args.classifier_task,
        )
        log(f"  r{round_index} classify: {time.perf_counter() - t:.1f}s -> {len(decisions)} tiles")
        # Map (row,col) decisions to exact DOM tile centers, skip already-selected.
        by_rc = {(t.row, t.col): t for t in dom.tiles}
        clicked = []
        t = time.perf_counter()
        for d in decisions:
            tile = by_rc.get((d.row, d.col))
            if tile is None or tile.selected:
                continue
            cx, cy = tile.center
            await wait_page_op(
                click_like_human(page, cx, cy, start=(gx + gw + 40, gy + gh + 40), steps=8, seed=args.seed + d.row * 10 + d.col, hold_ms=55, move_delay=0.008),
                timeout=8,
            )
            clicked.append([d.row, d.col])
        log(f"  r{round_index} clicked {len(clicked)} tiles in {time.perf_counter() - t:.1f}s")
        rec["rounds"].append({"round": round_index, "planned": len(decisions), "clicked": clicked})
        if not clicked:
            break
        if challenge_type == "dynamic":
            await asyncio.sleep(args.refill_wait)
        else:
            break
    return rec


async def wait_for_dom_grid(page: object, *, timeout: float) -> DomChallengeState:
    deadline = asyncio.get_event_loop().time() + timeout
    last = DomChallengeState(ok=False, present=False, reason="timeout")
    while asyncio.get_event_loop().time() < deadline:
        last = await read_dom_challenge_state(page)
        if last.ok and last.present and last.tiles:
            return last
        await asyncio.sleep(0.6)
    return last


async def click_verify(page: object, dom: DomChallengeState, args: argparse.Namespace) -> None:
    dom = await read_dom_challenge_state(page)
    if dom.verify:
        v = dom.verify
        await click_like_human(page, v["x"] + v["width"] / 2, v["y"] + v["height"] / 2, steps=9, seed=args.seed + 7, hold_ms=70)


async def read_token(page: object) -> str:
    return str(await page.eval_js(
        "document.querySelector('#g-recaptcha-response, textarea[name=\"g-recaptcha-response\"]')?.value || ''"
    ) or "")


async def read_token_after(page: object, wait: float) -> str:
    deadline = asyncio.get_event_loop().time() + wait
    while asyncio.get_event_loop().time() < deadline:
        token = await read_token(page)
        if token:
            return token
        await asyncio.sleep(0.5)
    return await read_token(page)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.google.com/recaptcha/api2/demo")
    parser.add_argument("--ws-url")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--wait-secs", type=float, default=12.0)
    parser.add_argument("--challenge-timeout", type=float, default=15.0)
    parser.add_argument("--pre-checkbox-wait", type=float, default=2.5)
    parser.add_argument("--post-verify-wait", type=float, default=6.0)
    parser.add_argument("--refill-wait", type=float, default=2.5)
    parser.add_argument("--max-rounds", type=int, default=8)
    parser.add_argument("--max-challenges", type=int, default=8)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--work-dir", default=".local/recaptcha/dom")
    parser.add_argument("--models", default="verytuffcat/recaptcha")
    parser.add_argument("--classifier-task", default="image-classification")
    parser.add_argument("--labels", default="bicycle,bus,car,crosswalk,hydrant,motorcycle,traffic light,bridge,boat,stair,chimney")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cache-dir", default=".local/hf")
    parser.add_argument("--ml-python", default="python")
    parser.add_argument("--min-consensus", type=float, default=1.0)
    parser.add_argument("--min-target-score", type=float, default=0.3)
    parser.add_argument("--min-score-margin", type=float, default=0.05)
    parser.add_argument("--run-timeout", type=float, default=600.0)
    args = parser.parse_args()

    try:
        payload = asyncio.run(asyncio.wait_for(solve(args), timeout=args.run_timeout))
    except TimeoutError:
        payload = {"url": args.url, "outcome": "run-timeout"}
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
