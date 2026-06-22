"""MTCaptcha — distorted-text-over-photo OCR, in-session token mint.

The 2Captcha MTCaptcha demo renders the *standard* MTCaptcha challenge inside a
cross-origin ``service.mtcaptcha.com`` iframe: a noisy alphanumeric word drawn
over a natural photo (``div.mtcap-image`` background-image GIF) plus a text
input. The useful deliverable is a real verified token — it lands in
``window.mtcaptcha.getVerifiedToken()`` / the page's
``input[name="mtcaptcha-verifiedtoken"]`` sink once a correct answer is typed.

Mechanics (no faked token):

1. Read the challenge *inside* the iframe via CDP frame eval — the word image as
   a same-origin GIF data URL and the answer input. This requires the frame to be
   in-process, so the session must launch with
   ``extra_args=["disable-site-isolation-trials"]`` (cross-origin OOPIF input is
   otherwise unreachable).
2. OCR the word. Text-over-photo is a *scene-text* problem, not document OCR, so
   a local scene-text recognizer (RapidOCR / PP-OCRv4) is preferred, with
   multi-PSM Tesseract as the fallback.
3. Focus the input in-frame and type with real CDP key events (read the value
   back to correct dropped keys).
4. Poll for the minted token; on a wrong guess MTCaptcha reships a fresh word and
   may pop a "failed / OK" alert, so dismiss it and retry up to ``max_attempts``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.policy import SolverPolicy
from OpenSesame.api.registry import ModelKey, ModelRegistry
from OpenSesame.api.result import (
    Family,
    SolvedBy,
    SolveResult,
    SolveStatus,
    Timing,
    TokenSolution,
)

MT_FRAME = "service.mtcaptcha.com"

MTCAPTCHA_TOKEN_JS = r"""
(() => {
  let token = '';
  try {
    if (window.mtcaptcha && window.mtcaptcha.getVerifiedToken) {
      token = window.mtcaptcha.getVerifiedToken() || '';
    }
  } catch (e) {}
  const sink = document.querySelector(
    'input.mtcaptcha-verifiedtoken, input[name="mtcaptcha-verifiedtoken"]'
  );
  if (!token && sink) token = sink.value || '';
  const cfg = window.mtcaptchaConfig || {};
  return {token: token, sitekey: cfg.sitekey || null};
})()
"""

# Runs INSIDE the MTCaptcha iframe (in-process via disable-site-isolation-trials).
MTCAPTCHA_FRAME_JS = r"""
(() => {
  const out = {ok: true};
  const div = document.querySelector('.mtcap-image');
  if (div) {
    const bi = getComputedStyle(div).backgroundImage || '';
    const m = bi.match(/url\((['"]?)(data:[^'")]+)\1\)/);
    out.image = {data: m ? m[2] : null};
  }
  const inp = document.querySelector('input.mtcap-inputtext, input[type="text"]');
  if (inp) {
    out.input_value = inp.value || '';
    try { inp.focus(); } catch (e) {}
  }
  return out;
})()
"""

MTCAPTCHA_FOCUS_JS = (
    "(() => { const i = document.querySelector('input.mtcap-inputtext, "
    "input[type=\"text\"]'); if (!i) return false; i.focus(); return true; })()"
)
MTCAPTCHA_CLEAR_JS = (
    "(() => { const i = document.querySelector('input.mtcap-inputtext, "
    "input[type=\"text\"]'); if (!i) return false; i.value = ''; "
    "i.dispatchEvent(new Event('input', {bubbles: true})); i.focus(); return true; })()"
)
MTCAPTCHA_READBACK_JS = (
    "(() => { const i = document.querySelector('input.mtcap-inputtext, "
    "input[type=\"text\"]'); return i ? (i.value || '') : null; })()"
)
MTCAPTCHA_DISMISS_ALERT_JS = (
    "(() => { const b = document.querySelector('input.mtcap-alert-btn, "
    ".mtcap-alert-btn, button.mtcap-alert-btn'); if (!b) return false; "
    "const r = b.getBoundingClientRect(); if (r.width>0 && r.height>0){ b.click(); return true; } "
    "return false; })()"
)


def decode_data_url(data_url: Any) -> bytes | None:
    """Decode a ``data:image/...;base64,...`` URL into raw bytes (gif/png agnostic)."""

    if not isinstance(data_url, str) or not data_url.startswith("data:image"):
        return None
    marker = ";base64,"
    idx = data_url.find(marker)
    if idx == -1:
        return None
    try:
        return base64.b64decode(data_url[idx + len(marker):], validate=False)
    except (binascii.Error, ValueError):
        return None


def plausible_answer(text: str, *, lo: int = 4, hi: int = 10) -> bool:
    return bool(text) and lo <= len(text) <= hi


def default_scenetext_python() -> str | None:
    """Best-effort path to a RapidOCR-capable interpreter, if provisioned."""

    candidates = [
        Path(".local/venvs/scenetext/bin/python"),
        Path.home() / "Desktop/cl/OpenSesame/.local/venvs/scenetext/bin/python",
    ]
    env = os.environ.get("OPENSESAME_SCENETEXT_PYTHON")
    if env:
        candidates.insert(0, Path(env))
    for c in candidates:
        if c.exists():
            return str(c)
    return None


# --- OCR -------------------------------------------------------------------

_SCENETEXT_CODE = r"""
import sys, json
import numpy as np
from PIL import Image, ImageEnhance
from rapidocr_onnxruntime import RapidOCR
_ocr = RapidOCR()
def read(arr):
    res, _ = _ocr(arr)
    if not res:
        return '', 0.0
    res = sorted(res, key=lambda r: r[0][0][0])
    return ''.join(r[1] for r in res), sum(r[2] for r in res) / len(res)
im = Image.open(sys.argv[1]).convert('RGB')
best_t, best_c = '', 0.0
for arr in (np.array(im), np.array(ImageEnhance.Contrast(im).enhance(1.8))):
    t, c = read(arr)
    if c > best_c:
        best_t, best_c = t, c
print(json.dumps({'text': best_t, 'conf': best_c}))
"""

_PREPROCESS_CODE = r"""
import sys
from PIL import Image, ImageOps, ImageFilter
src, dst = sys.argv[1:3]
im = Image.open(src).convert('L')
w, h = im.size
scale = max(1, int(round(220.0 / max(1, h))))
im = im.resize((w * scale, h * scale), Image.LANCZOS)
im = ImageOps.autocontrast(im).filter(ImageFilter.MedianFilter(3))
im.save(dst)
"""

_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_ALNUM = __import__("re").compile(r"[^A-Za-z0-9]+")


def _normalize(text: str) -> str:
    return _ALNUM.sub("", text).strip()


def read_scenetext(image_path: Path, scenetext_python: str) -> tuple[str, float]:
    try:
        out = subprocess.run(
            [scenetext_python, "-c", _SCENETEXT_CODE, str(image_path)],
            capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return "", 0.0
    lines = (out.stdout or "").strip().splitlines()
    if not lines:
        return "", 0.0
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return "", 0.0
    return _normalize(str(data.get("text") or "")), float(data.get("conf") or 0.0)


def read_tesseract(image_path: Path, *, tesseract_cmd: str, ml_python: str) -> tuple[str, float]:
    pre = image_path.with_name(image_path.stem + "-pre.png")
    try:
        subprocess.run([ml_python, "-c", _PREPROCESS_CODE, str(image_path), str(pre)],
                       check=True, capture_output=True, text=True)
        target = pre
    except (OSError, subprocess.CalledProcessError):
        target = image_path
    best_text, best_conf = "", 0.0
    for psm in (7, 8, 6, 13):
        try:
            out = subprocess.run(
                [tesseract_cmd, str(target), "stdout", "--psm", str(psm),
                 "-c", f"tessedit_char_whitelist={_WHITELIST}", "tsv"],
                check=True, capture_output=True, text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        text, conf = _parse_tsv(out.stdout)
        score = conf + (0.25 if plausible_answer(text) else -0.25)
        if score > best_conf:
            best_text, best_conf = text, conf
    return best_text, best_conf


def _parse_tsv(tsv: str) -> tuple[str, float]:
    import csv

    words, confs = [], []
    for row in csv.DictReader(tsv.splitlines(), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        words.append(text)
        try:
            c = float((row.get("conf") or "").strip())
        except ValueError:
            continue
        if c >= 0:
            confs.append(c / 100.0)
    if not words:
        return "", 0.0
    return _normalize("".join(words)), (sum(confs) / len(confs) if confs else 0.0)


def ocr_word(image_path: Path, *, scenetext_python: str | None, tesseract_cmd: str,
             ml_python: str) -> tuple[str, float]:
    if scenetext_python:
        text, conf = read_scenetext(image_path, scenetext_python)
        if plausible_answer(text):
            return text, conf
    return read_tesseract(image_path, tesseract_cmd=tesseract_cmd, ml_python=ml_python)


class MtcaptchaEngine:
    """MTCaptcha: OCR the word-over-photo, type it in-frame, harvest the token."""

    family = Family.MTCAPTCHA

    def __init__(
        self,
        *,
        scenetext_python: str | None = None,
        ml_python: str | None = None,
        tesseract_cmd: str = "tesseract",
        max_attempts: int = 8,
        token_wait_s: float = 12.0,
        work_dir: str = ".local/mtcaptcha",
    ) -> None:
        self.scenetext_python = scenetext_python or default_scenetext_python()
        self.ml_python = ml_python or os.environ.get("OPENSESAME_ML_PYTHON") or "python3"
        self.tesseract_cmd = tesseract_cmd
        self.max_attempts = max_attempts
        self.token_wait_s = token_wait_s
        self.work_dir = work_dir

    def model_keys(self, policy: SolverPolicy) -> list[ModelKey]:
        return []  # scene-text/OCR run out-of-process; no registry model

    async def solve(
        self,
        challenge: Challenge,
        page: Any,
        *,
        registry: ModelRegistry,
        policy: SolverPolicy,
        correlation_id: str | None = None,
    ) -> SolveResult:
        started = time.time()
        work = Path(self.work_dir)
        work.mkdir(parents=True, exist_ok=True)
        ocr_trace: list[dict[str, Any]] = []

        state = await page.eval_js(MTCAPTCHA_TOKEN_JS)
        sitekey = state.get("sitekey") if isinstance(state, dict) else None
        if isinstance(state, dict) and state.get("token"):
            return self._ok(challenge, str(state["token"]), sitekey, started, ocr_trace)

        for index in range(1, self.max_attempts + 1):
            await self._eval_frame(page, MTCAPTCHA_DISMISS_ALERT_JS)

            frame = await self._eval_frame(page, MTCAPTCHA_FRAME_JS)
            data = frame.get("image", {}).get("data") if isinstance(frame, dict) else None
            png = decode_data_url(data)
            if png is None:
                return self._fail(
                    challenge,
                    "MTCaptcha widget frame unreadable — launch the session with "
                    'extra_args=["disable-site-isolation-trials"] so the cross-origin '
                    "frame stays in-process.",
                    started, ocr_trace, frame_isolated=True,
                )

            image_path = work / f"mt-word-{index}.png"
            image_path.write_bytes(png)
            text, conf = ocr_word(
                image_path,
                scenetext_python=self.scenetext_python,
                tesseract_cmd=self.tesseract_cmd,
                ml_python=self.ml_python,
            )
            ocr_trace.append({"attempt": index, "text": text, "conf": round(conf, 3)})
            if not plausible_answer(text):
                await asyncio.sleep(1.0)
                continue

            await self._eval_frame(page, MTCAPTCHA_CLEAR_JS)
            await self._eval_frame(page, MTCAPTCHA_FOCUS_JS)
            await self._type(page, text)
            await asyncio.sleep(0.2)
            landed = str(await self._eval_frame(page, MTCAPTCHA_READBACK_JS) or "")
            if landed.strip().lower() != text.strip().lower():
                # one retype pass for dropped keys
                await self._eval_frame(page, MTCAPTCHA_CLEAR_JS)
                await self._eval_frame(page, MTCAPTCHA_FOCUS_JS)
                await self._type(page, text)
                await asyncio.sleep(0.2)

            token = await self._poll_token(page)
            if token:
                return self._ok(challenge, token, sitekey, started, ocr_trace)
            await asyncio.sleep(1.4)

        return self._fail(challenge, "no verified token after OCR attempts", started, ocr_trace)

    # -- internals --------------------------------------------------------

    async def _eval_frame(self, page: Any, expr: str) -> Any:
        try:
            return await page.eval_js_in_frame(MT_FRAME, expr)
        except Exception:
            return {}

    async def _type(self, page: Any, text: str, *, delay: float = 0.11) -> None:
        for ch in text:
            await page.dispatch_key_event("keyDown", key=ch, text=ch)
            await page.dispatch_key_event("keyUp", key=ch)
            await asyncio.sleep(delay)

    async def _poll_token(self, page: Any) -> str:
        deadline = time.time() + self.token_wait_s
        while time.time() < deadline:
            state = await page.eval_js(MTCAPTCHA_TOKEN_JS)
            if isinstance(state, dict) and state.get("token"):
                return str(state["token"])
            await asyncio.sleep(0.6)
        return ""

    def _ok(self, challenge, token, sitekey, started, ocr_trace) -> SolveResult:
        return SolveResult(
            status=SolveStatus.SOLVED, family=Family.MTCAPTCHA,
            solution=TokenSolution(token), solved_by=SolvedBy.LOCAL,
            vendor="mtcaptcha",
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata={"strategy": "mtcaptcha-ocr", "sitekey": sitekey, "ocr": ocr_trace,
                      "attempts": len(ocr_trace)},
        )

    def _fail(self, challenge, error, started, ocr_trace, *, frame_isolated: bool = False) -> SolveResult:
        md: dict[str, Any] = {"strategy": "mtcaptcha-ocr", "ocr": ocr_trace}
        if frame_isolated:
            md["frame_isolated"] = True
        return SolveResult(
            status=SolveStatus.FAILED, family=Family.MTCAPTCHA, error=error,
            timing=Timing(started_at=started, elapsed_ms=(time.time() - started) * 1000.0),
            metadata=md,
        )
