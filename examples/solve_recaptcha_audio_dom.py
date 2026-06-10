#!/usr/bin/env python3
"""Audio-side-door reCAPTCHA solve on Google's same-origin api2/demo.

Flow: click the checkbox, switch to the audio challenge, read the signed MP3 URL
from the same-origin bframe DOM, download it, transcribe it with a *local*
Whisper model (no paid API), type the transcript into the response field via CDP
key events, verify, and read the minted token from the parent textarea.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from open_sesame.harness.page_sync import SerializedPage, wait_page_op
from open_sesame.harness.recaptcha_v2 import attempt_recaptcha_v2_checkbox
from open_sesame.solvers.audio import normalize_asr_text

_START = time.perf_counter()


def log(msg: str) -> None:
    print(f"[t={time.perf_counter() - _START:5.1f}s] {msg}", file=sys.stderr, flush=True)


# Read the audio-challenge state from the same-origin bframe.
AUDIO_DOM_JS = r"""
(() => {
  const f = document.querySelector('iframe[src*="api2/bframe"]');
  if (!f || !f.contentDocument) return {ok:false, reason:'no-bframe'};
  const doc = f.contentDocument;
  const dl = doc.querySelector('.rc-audiochallenge-tdownload-link');
  const src = doc.querySelector('#audio-source');
  const resp = doc.querySelector('#audio-response');
  const verify = doc.querySelector('#recaptcha-verify-button');
  const blocked = doc.querySelector('.rc-doscaptcha-header, .rc-doscaptcha-body');
  const tokenEl = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
  return {
    ok: true,
    download: dl ? dl.href : (src ? src.src : null),
    has_response: !!resp,
    has_verify: !!verify,
    rate_limited: !!blocked,
    token: tokenEl ? (tokenEl.value || '') : '',
  };
})()
"""

CLICK_AUDIO_BUTTON_JS = r"""
(() => {
  const f = document.querySelector('iframe[src*="api2/bframe"]');
  if (!f || !f.contentDocument) return false;
  const b = f.contentDocument.querySelector('#recaptcha-audio-button');
  if (b) { b.click(); return true; }
  return false;
})()
"""

FOCUS_RESPONSE_JS = r"""
(() => {
  const f = document.querySelector('iframe[src*="api2/bframe"]');
  const el = f && f.contentDocument && f.contentDocument.querySelector('#audio-response');
  if (!el) return false;
  el.focus(); el.value = '';
  return true;
})()
"""

CLICK_VERIFY_JS = r"""
(() => {
  const f = document.querySelector('iframe[src*="api2/bframe"]');
  const el = f && f.contentDocument && f.contentDocument.querySelector('#recaptcha-verify-button');
  if (!el) return false;
  el.click(); return true;
})()
"""


async def solve(args: argparse.Namespace) -> dict[str, Any]:
    from voidcrawl import BrowserConfig, BrowserSession

    if args.ws_url:
        config = BrowserConfig(ws_url=args.ws_url)
    else:
        config = BrowserConfig(headless=not args.headful, stealth=True, extra_args=["--window-size=1365,900"])

    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)

    recognizer = load_recognizer(args)
    log(f"ASR model loaded ({args.asr_model})")

    runs: list[dict[str, Any]] = []
    async with BrowserSession(config) as browser:
        page = SerializedPage(await browser.new_page("about:blank"))
        for run_index in range(1, args.repeat + 1):
            t0 = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    solve_once(page, recognizer, args, work, run_index),
                    timeout=args.per_solve_timeout,
                )
            except (TimeoutError, Exception) as exc:  # keep the batch going
                result = {"outcome": "error", "error": f"{type(exc).__name__}: {exc}"}
            result["seconds"] = round(time.perf_counter() - t0, 1)
            result["run"] = run_index
            runs.append(result)
            log(f"run {run_index}: {result.get('outcome')} in {result['seconds']}s")
            # Surface (do not control) rate-limiting for downstream rotation.
            if result.get("rate_limited"):
                log("reCAPTCHA audio RATE LIMITED — downstream should rotate proxy/profile")

    passed = [r for r in runs if r.get("token_present")]
    rl = next((r["run"] for r in runs if r.get("rate_limited")), None)
    return {
        "url": args.url,
        "asr_model": args.asr_model,
        "runs": runs,
        "passes": len(passed),
        "total": len(runs),
        "avg_seconds_on_pass": round(sum(r["seconds"] for r in passed) / len(passed), 1) if passed else None,
        "rate_limited_first_at_run": rl,
    }


async def solve_once(
    page: object,
    recognizer: Any,
    args: argparse.Namespace,
    work: Path,
    run_index: int,
) -> dict[str, Any]:
    record: dict[str, Any] = {"attempts": []}
    await wait_page_op(page.goto(args.url, timeout=args.timeout), timeout=args.timeout + 5)
    try:
        await wait_page_op(page.wait_for_network_idle(timeout=args.timeout), timeout=args.timeout + 2)
    except Exception:
        pass

    st = await wait_page_op(
        attempt_recaptcha_v2_checkbox(page, wait_secs=args.wait_secs),
        timeout=args.wait_secs + 15,
    )
    token = await read_token(page)
    if token:
        return done(record, token, "passed-on-checkbox")

    await wait_page_op(page.eval_js(CLICK_AUDIO_BUTTON_JS), timeout=6)
    await asyncio.sleep(1.2)

    for attempt in range(1, args.max_attempts + 1):
        dom = await wait_for_audio(page, timeout=args.challenge_timeout)
        if isinstance(dom, dict) and dom.get("rate_limited"):
            record["rate_limited"] = True
            return done(record, "", "rate-limited")
        if not isinstance(dom, dict) or not dom.get("download"):
            record["attempts"].append({"attempt": attempt, "error": "no-download"})
            break

        mp3 = work / f"run{run_index}-audio{attempt}.mp3"
        await fetch_mp3(page, dom["download"], mp3)
        t = time.perf_counter()
        raw = await asyncio.to_thread(recognizer, str(mp3))
        text = normalize_answer(extract_text(raw))
        asr_secs = round(time.perf_counter() - t, 1)

        await type_answer(page, text)
        await wait_page_op(page.eval_js(CLICK_VERIFY_JS), timeout=8)
        token = await read_token_after(page, args.post_verify_wait)
        record["attempts"].append({"attempt": attempt, "transcript": text, "asr_secs": asr_secs, "token_after": bool(token)})
        if token:
            record["transcript"] = text
            if args.record:
                shot = work / f"run{run_index}-passed.png"
                try:
                    await wait_page_op(page.screenshot(path=str(shot)), timeout=8)
                    record["screenshot"] = str(shot)
                except Exception:
                    pass
            return done(record, token, "passed")
        await asyncio.sleep(1.2)

    return done(record, await read_token(page), "no-token")


def load_recognizer(args: argparse.Namespace):
    import os
    os.environ.setdefault("HF_HOME", args.cache_dir)
    from transformers import pipeline

    return pipeline(
        task="automatic-speech-recognition",
        model=args.asr_model,
        device=-1,
        model_kwargs={"cache_dir": args.cache_dir},
    )


def extract_text(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("text", ""))
    if isinstance(raw, list) and raw:
        return extract_text(raw[0])
    return str(raw)


def normalize_answer(text: str) -> str:
    # reCAPTCHA audio answers are short digit/word sequences; strip punctuation,
    # lowercase, collapse whitespace.
    cleaned = re.sub(r"[^\w\s]", " ", normalize_asr_text(text)).lower()
    return " ".join(cleaned.split())


async def fetch_mp3(page: object, url: str, out: Path) -> None:
    # The payload URL is signed (p=) and authorized by IP, so a same-host GET
    # works and keeps the solve on the live session's network path.
    def _get() -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()

    data = await asyncio.to_thread(_get)
    out.write_bytes(data)
    log(f"downloaded mp3: {len(data)} bytes")


async def type_answer(page: object, text: str) -> None:
    focused = await wait_page_op(page.eval_js(FOCUS_RESPONSE_JS), timeout=6)
    if not focused:
        log("WARN: could not focus #audio-response")
        return
    # Type via CDP key events so reCAPTCHA sees real input on the focused field.
    for ch in text:
        await page.dispatch_key_event("keyDown", key=ch, text=ch)
        await page.dispatch_key_event("keyUp", key=ch)
        await asyncio.sleep(0.03)


async def wait_for_audio(page: object, *, timeout: float) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {"ok": False, "reason": "timeout"}
    while asyncio.get_event_loop().time() < deadline:
        last = await page.eval_js(AUDIO_DOM_JS)
        if isinstance(last, dict) and last.get("ok") and (last.get("download") or last.get("rate_limited")):
            return last
        await asyncio.sleep(0.5)
    return last if isinstance(last, dict) else {"ok": False, "reason": "timeout"}


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


def done(record: dict[str, Any], token: str, outcome: str) -> dict[str, Any]:
    record["outcome"] = outcome
    record["token_present"] = bool(token)
    record["token_length"] = len(token)
    if token:
        record["token_preview"] = token[:60] + "..."
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.google.com/recaptcha/api2/demo")
    parser.add_argument("--ws-url")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--asr-model", default="openai/whisper-small.en")
    parser.add_argument("--cache-dir", default=".local/hf")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--wait-secs", type=float, default=6.0)
    parser.add_argument("--challenge-timeout", type=float, default=12.0)
    parser.add_argument("--post-verify-wait", type=float, default=6.0)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--repeat", type=int, default=1, help="Consecutive solves in one resident session.")
    parser.add_argument("--record", action="store_true", help="Save a screenshot on each successful solve.")
    parser.add_argument("--per-solve-timeout", type=float, default=90.0)
    parser.add_argument("--work-dir", default=".local/recaptcha/audio")
    parser.add_argument("--run-timeout", type=float, default=600.0)
    args = parser.parse_args()

    try:
        payload = asyncio.run(asyncio.wait_for(solve(args), timeout=args.run_timeout))
    except TimeoutError:
        payload = {"url": args.url, "outcome": "run-timeout"}
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
