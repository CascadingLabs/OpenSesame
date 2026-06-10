"""Live MTCaptcha solver: OCR the distorted-text variant, mint the token.

The 2Captcha MTCaptcha demo (``2captcha.com/demo/mtcaptcha``) renders the
distorted-text variant of MTCaptcha inside a cross-origin
``service.mtcaptcha.com`` iframe: a single noisy word image plus a text input.
This is the OCR-fallback path of CAS-185 and reuses the CAS-170 OCR service.

The useful deliverable is a real MTCaptcha verified token minted in the live
session: it lands in ``window.mtcaptcha.getVerifiedToken()`` and the page's
``input[name="mtcaptcha-verifiedtoken"]`` sink once the typed answer verifies.

Strategy (cross-origin, no faked token):

1. Read main-world state: sitekey, token sink, iframe screen rect.
2. Read the challenge *inside* the iframe via CDP frame-scoped eval — grab the
   word image as a PNG data URL and the answer input's frame-local rect. If the
   frame is opaque to eval, fall back to a screenshot crop of the image band.
3. OCR the word image (multi-PSM Tesseract over a preprocessed crop).
4. Focus the input (frame ``focus()`` + a trusted coordinate click) and type the
   answer with real CDP key events so MTCaptcha's own verifier runs.
5. Poll for the minted token; on a wrong guess MTCaptcha reships a new image, so
   retry up to ``max_attempts``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Main-world state: sitekey, current verified token, and the widget iframe rect
# (top-level viewport CSS coords) used to place coordinate clicks / crops.
MTCAPTCHA_STATE_JS = r"""
(() => {
  const cfg = window.mtcaptchaConfig || {};
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
  const frame = document.querySelector(
    'iframe[id^="mtcaptcha-iframe"], iframe[src*="mtcaptcha"]'
  );
  let rect = null;
  if (frame) {
    const r = frame.getBoundingClientRect();
    rect = {x: r.left, y: r.top, width: r.width, height: r.height};
  }
  return {
    sitekey: cfg.sitekey || null,
    token: token,
    has_api: typeof window.mtcaptcha,
    iframe: rect,
  };
})()
"""


# Runs INSIDE the MTCaptcha iframe via CDP frame eval. The widget must be
# reachable as an in-process frame (launch Chrome with site isolation disabled —
# see ``MTCAPTCHA_NO_ISOLATION_ARGS``), otherwise the cross-origin frame is an
# opaque OOPIF. MTCaptcha renders the word as a ``background-image`` GIF data URL
# on ``div.mtcap-image`` and takes the answer in ``input.mtcap-inputtext``.
MTCAPTCHA_FRAME_JS = r"""
(() => {
  const out = {ok: true};
  const div = document.querySelector('.mtcap-image');
  if (div) {
    const bi = getComputedStyle(div).backgroundImage || '';
    const m = bi.match(/url\((['"]?)(data:[^'")]+)\1\)/);
    const r = div.getBoundingClientRect();
    out.image = {
      x: r.left, y: r.top, width: r.width, height: r.height,
      data: m ? m[2] : null,
    };
  }
  const inp = document.querySelector('input.mtcap-inputtext, input[type="text"]');
  if (inp) {
    const r = inp.getBoundingClientRect();
    out.input = {x: r.left, y: r.top, width: r.width, height: r.height};
    out.input_value = inp.value || '';
    try { inp.focus(); } catch (e) {}
  }
  return out;
})()
"""


# Launch flags that drop Chrome site isolation so the cross-origin MTCaptcha
# widget renders in-process: then CDP frame eval reaches it and CDP key events
# route to its focused input. Does not weaken the solve — the token is still
# minted by MTCaptcha after a correct answer.
MTCAPTCHA_NO_ISOLATION_ARGS = (
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials",
)


# Focus the answer input (kept separate so we can re-assert focus right before
# typing, since each CDP call is independent).
MTCAPTCHA_FOCUS_JS = (
    "(() => { const i = document.querySelector('input.mtcap-inputtext, "
    "input[type=\"text\"]'); if (!i) return false; i.focus(); return true; })()"
)

# Clear the answer input in-frame before retyping.
MTCAPTCHA_CLEAR_JS = (
    "(() => { const i = document.querySelector('input.mtcap-inputtext, "
    "input[type=\"text\"]'); if (!i) return false; i.value = ''; "
    "i.dispatchEvent(new Event('input', {bubbles: true})); i.focus(); return true; })()"
)

# Read the current answer-input value back to confirm typing landed.
MTCAPTCHA_READBACK_JS = (
    "(() => { const i = document.querySelector('input.mtcap-inputtext, "
    "input[type=\"text\"]'); return i ? (i.value || '') : null; })()"
)

# After repeated misses MTCaptcha pops a "verification failed / OK" alert that
# covers the input. Dismiss it so the loop can keep trying fresh words.
MTCAPTCHA_DISMISS_ALERT_JS = r"""
(() => {
  const btn = document.querySelector('input.mtcap-alert-btn, .mtcap-alert-btn, button.mtcap-alert-btn');
  if (btn) {
    const r = btn.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) { btn.click(); return true; }
  }
  return false;
})()
"""


@dataclass(frozen=True)
class MtRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


@dataclass(frozen=True)
class MtCaptchaState:
    sitekey: str | None
    token: str
    has_api: bool
    iframe: MtRect | None

    @property
    def solved(self) -> bool:
        return bool(self.token)


def parse_mt_state(raw: Any) -> MtCaptchaState:
    if not isinstance(raw, dict):
        return MtCaptchaState(sitekey=None, token="", has_api=False, iframe=None)
    iframe = raw.get("iframe")
    return MtCaptchaState(
        sitekey=raw.get("sitekey"),
        token=str(raw.get("token") or ""),
        has_api=raw.get("has_api") == "object",
        iframe=_rect(iframe),
    )


def _rect(value: Any) -> MtRect | None:
    if not isinstance(value, dict):
        return None
    try:
        return MtRect(
            x=float(value["x"]),
            y=float(value["y"]),
            width=float(value["width"]),
            height=float(value["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def decode_data_url_png(data_url: str) -> bytes | None:
    """Decode a ``data:image/png;base64,...`` URL into raw PNG bytes."""

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
    """MTCaptcha words are short alnum tokens; reject obvious OCR noise."""

    return bool(text) and lo <= len(text) <= hi


@dataclass(frozen=True)
class MtCaptchaAttempt:
    index: int
    ocr_text: str
    ocr_confidence: float
    image_source: str  # "frame-canvas" | "screenshot-crop" | "none"
    typed: bool
    token: str | None
    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def solved(self) -> bool:
        return bool(self.token)


@dataclass(frozen=True)
class MtCaptchaResult:
    solved: bool
    token: str | None
    sitekey: str | None
    attempts: tuple[MtCaptchaAttempt, ...]
    elapsed_ms: float
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "solved": self.solved,
            "token": self.token,
            "token_length": len(self.token) if self.token else 0,
            "sitekey": self.sitekey,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error": self.error,
            "attempts": [
                {
                    "index": a.index,
                    "ocr_text": a.ocr_text,
                    "ocr_confidence": round(a.ocr_confidence, 3),
                    "image_source": a.image_source,
                    "typed": a.typed,
                    "token_minted": a.solved,
                    "signals": list(a.signals),
                }
                for a in self.attempts
            ],
        }


MT_FRAME_URL_PATTERN = "service.mtcaptcha.com"


async def read_mt_state(page: Any) -> MtCaptchaState:
    return parse_mt_state(await page.eval_js(MTCAPTCHA_STATE_JS))


async def read_mt_frame_challenge(page: Any) -> dict[str, Any]:
    """Read the in-iframe word image + input rect via CDP frame eval.

    Returns ``{}`` when the frame cannot be evaluated (opaque cross-origin).
    """

    try:
        raw = await page.eval_js_in_frame(MT_FRAME_URL_PATTERN, MTCAPTCHA_FRAME_JS)
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


async def type_answer(page: Any, answer: str, *, per_key_delay: float = 0.11) -> None:
    """Type an answer into the focused input with real CDP key events.

    The per-key delay matters: too fast and Chrome drops keys before the
    in-frame input handler runs.
    """

    for ch in answer:
        await page.dispatch_key_event("keyDown", key=ch, text=ch)
        await page.dispatch_key_event("keyUp", key=ch)
        if per_key_delay > 0:
            await asyncio.sleep(per_key_delay)


async def focus_answer_input(page: Any) -> bool:
    try:
        return bool(await page.eval_js_in_frame(MT_FRAME_URL_PATTERN, MTCAPTCHA_FOCUS_JS))
    except Exception:
        return False


async def clear_answer_input(page: Any) -> bool:
    try:
        return bool(await page.eval_js_in_frame(MT_FRAME_URL_PATTERN, MTCAPTCHA_CLEAR_JS))
    except Exception:
        return False


async def read_answer_input(page: Any) -> str:
    try:
        value = await page.eval_js_in_frame(MT_FRAME_URL_PATTERN, MTCAPTCHA_READBACK_JS)
    except Exception:
        return ""
    return str(value or "")


async def type_answer_verified(page: Any, answer: str, *, attempts: int = 3) -> str:
    """Type the answer, reading it back in-frame and correcting dropped keys."""

    for _ in range(attempts):
        await clear_answer_input(page)
        await focus_answer_input(page)
        await type_answer(page, answer)
        await asyncio.sleep(0.2)
        landed = await read_answer_input(page)
        if landed.strip().lower() == answer.strip().lower():
            return landed
    return await read_answer_input(page)


# --- OCR -------------------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402

from open_sesame.solvers.ocr import normalize_ocr_text, parse_tesseract_tsv  # noqa: E402


# Scene-text reader (RapidOCR / PP-OCRv4) — runs in a dedicated venv because the
# word is rendered OVER a natural photo, which document-OCR (Tesseract) cannot
# read but a scene-text recognizer handles well. On-box, no paid API.
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
    res = sorted(res, key=lambda r: r[0][0][0])  # left-to-right boxes
    text = ''.join(r[1] for r in res)
    conf = sum(r[2] for r in res) / len(res)
    return text, conf

im = Image.open(sys.argv[1]).convert('RGB')
variants = [np.array(im), np.array(ImageEnhance.Contrast(im).enhance(1.8))]
best_t, best_c = '', 0.0
for arr in variants:
    t, c = read(arr)
    if c > best_c:
        best_t, best_c = t, c
print(json.dumps({'text': best_t, 'conf': best_c}))
"""


def default_scenetext_python() -> str | None:
    """Best-effort path to the scene-text OCR interpreter, if provisioned."""

    candidate = Path(".local/venvs/scenetext/bin/python")
    return str(candidate) if candidate.exists() else None


def read_scenetext(image_path: str | Path, scenetext_python: str) -> tuple[str, float]:
    """Read text-over-photo with RapidOCR via its venv. Returns (text, conf)."""

    try:
        completed = subprocess.run(
            [scenetext_python, "-c", _SCENETEXT_CODE, str(image_path)],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return "", 0.0
    line = (completed.stdout or "").strip().splitlines()
    if not line:
        return "", 0.0
    try:
        data = json.loads(line[-1])
    except json.JSONDecodeError:
        return "", 0.0
    return normalize_ocr_text(str(data.get("text") or "")), float(data.get("conf") or 0.0)

_PREPROCESS_CODE = r"""
import sys
from PIL import Image, ImageOps, ImageFilter
import numpy as np

src, gray_dst, bw_dst = sys.argv[1:4]
im = Image.open(src).convert('L')
w, h = im.size
scale = max(1, int(round(220.0 / max(1, h))))  # target ~220px tall for Tesseract
im = im.resize((w * scale, h * scale), Image.LANCZOS)
im = ImageOps.autocontrast(im)
im = im.filter(ImageFilter.MedianFilter(3))
im.save(gray_dst)

arr = np.asarray(im).astype('uint8')
hist = np.bincount(arr.ravel(), minlength=256).astype(float)
total = float(arr.size)
sum_all = float(np.dot(np.arange(256), hist))
sumB = 0.0
wB = 0.0
maximum = 0.0
threshold = 128
for i in range(256):
    wB += hist[i]
    if wB == 0:
        continue
    wF = total - wB
    if wF == 0:
        break
    sumB += i * hist[i]
    mB = sumB / wB
    mF = (sum_all - sumB) / wF
    between = wB * wF * (mB - mF) ** 2
    if between >= maximum:
        maximum = between
        threshold = i
# Letters darker than background -> ink is black on white.
bw = ((arr > threshold).astype('uint8')) * 255
Image.fromarray(bw).save(bw_dst)
"""


def preprocess_variants(
    image_path: str | Path,
    *,
    ml_python: str | None = None,
) -> list[Path]:
    """Produce grayscale + binarized OCR variants of a word image.

    Runs in a PIL/numpy-capable interpreter (``ml_python``) because the live
    VoidCrawl venv has neither. Returns the variant paths that were created;
    falls back to the original image if preprocessing is unavailable.
    """

    src = Path(image_path)
    python = ml_python or os.environ.get("OPENSESAME_ML_PYTHON") or "python3"
    gray = src.with_name(src.stem + "-gray.png")
    bw = src.with_name(src.stem + "-bw.png")
    try:
        subprocess.run(
            [python, "-c", _PREPROCESS_CODE, str(src), str(gray), str(bw)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return [src]
    return [p for p in (gray, bw) if p.exists() and p.stat().st_size > 0] or [src]


_ALNUM_WHITELIST = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)


def _tesseract_read(
    image_path: Path,
    *,
    tesseract_cmd: str,
    psm: int,
) -> tuple[str, float]:
    try:
        completed = subprocess.run(
            [
                tesseract_cmd,
                str(image_path),
                "stdout",
                "--psm",
                str(psm),
                "-c",
                f"tessedit_char_whitelist={_ALNUM_WHITELIST}",
                "tsv",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "", 0.0
    raw, conf = parse_tesseract_tsv(completed.stdout)
    return normalize_ocr_text(raw), conf


def ocr_mtcaptcha_word(
    image_path: str | Path,
    *,
    tesseract_cmd: str = "tesseract",
    ml_python: str | None = None,
    scenetext_python: str | None = None,
    psms: tuple[int, ...] = (7, 8, 6, 13),
) -> tuple[str, float]:
    """OCR a single MTCaptcha word image.

    Prefers a scene-text recognizer (RapidOCR) when provisioned — MTCaptcha
    renders the word over a natural photo, which document-OCR cannot read.
    Falls back to multi-PSM Tesseract over preprocessed crops.
    Returns ``(best_text, confidence)``, preferring plausible-length answers.
    """

    if scenetext_python:
        text, conf = read_scenetext(image_path, scenetext_python)
        if plausible_answer(text):
            return text, conf

    variants = preprocess_variants(image_path, ml_python=ml_python)
    best_text, best_conf, best_score = "", 0.0, -1.0
    for variant in variants:
        for psm in psms:
            text, conf = _tesseract_read(variant, tesseract_cmd=tesseract_cmd, psm=psm)
            if not text:
                continue
            # Reward plausible lengths so a confident 2-char misread loses to a
            # slightly-less-confident full word.
            score = conf + (0.25 if plausible_answer(text) else -0.25)
            if score > best_score:
                best_text, best_conf, best_score = text, conf, score
    return best_text, best_conf


# --- live driver -----------------------------------------------------------


async def solve_mtcaptcha(
    page: Any,
    *,
    work_dir: str | Path,
    tesseract_cmd: str = "tesseract",
    ml_python: str | None = None,
    scenetext_python: str | None = None,
    max_attempts: int = 4,
    token_wait: float = 14.0,
    on_event: Any = None,
) -> MtCaptchaResult:
    """Drive a live MTCaptcha text challenge to a minted verified token."""

    started = time.perf_counter()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    attempts: list[MtCaptchaAttempt] = []

    def emit(message: str) -> None:
        if on_event is not None:
            on_event(message)

    try:
        state = await read_mt_state(page)
        sitekey = state.sitekey
        if state.solved:
            return MtCaptchaResult(True, state.token, sitekey, (), _ms(started))

        for index in range(1, max_attempts + 1):
            signals: list[str] = []

            try:
                if await page.eval_js_in_frame(MT_FRAME_URL_PATTERN, MTCAPTCHA_DISMISS_ALERT_JS):
                    signals.append("dismissed-alert")
                    await asyncio.sleep(0.4)
            except Exception:
                pass

            frame = await read_mt_frame_challenge(page)
            image_path: Path | None = None
            image_source = "none"
            if frame and isinstance(frame.get("image"), dict) and frame["image"].get("data"):
                png = decode_data_url_png(frame["image"]["data"])
                if png:
                    image_path = work / f"mt-word-{index}.png"
                    image_path.write_bytes(png)
                    image_source = "frame-bgimage"
                    signals.append("frame-image-read")

            if image_path is None:
                # The widget frame is opaque — Chrome is enforcing site isolation
                # (OOPIF), so neither the image nor keystrokes are reachable. This
                # is the CAS-212 cross-origin condition; launch with
                # MTCAPTCHA_NO_ISOLATION_ARGS.
                attempts.append(
                    MtCaptchaAttempt(index, "", 0.0, "none", False, None, ("frame-unreadable",))
                )
                emit(f"attempt {index}: MTCaptcha frame unreadable (site isolation on?)")
                break

            text, conf = ocr_mtcaptcha_word(
                image_path,
                tesseract_cmd=tesseract_cmd,
                ml_python=ml_python,
                scenetext_python=scenetext_python,
            )
            signals.append(f"ocr={text or 'none'}@{conf:.2f}")
            emit(f"attempt {index}: OCR read '{text}' ({conf:.2f}) via {image_source}")

            typed = False
            if plausible_answer(text):
                landed = await type_answer_verified(page, text)
                typed = landed.strip().lower() == text.strip().lower()
                signals.append(f"typed={landed!r}" if typed else f"type-mismatch={landed!r}")
            else:
                signals.append("answer-implausible")

            token = None
            if typed:
                deadline = time.perf_counter() + token_wait
                while time.perf_counter() < deadline:
                    live = await read_mt_state(page)
                    if live.token:
                        token = live.token
                        break
                    await asyncio.sleep(0.6)

            attempts.append(
                MtCaptchaAttempt(index, text, conf, image_source, typed, token, tuple(signals))
            )
            if token:
                emit(f"attempt {index}: TOKEN minted (len {len(token)})")
                return MtCaptchaResult(True, token, sitekey, tuple(attempts), _ms(started))

            emit(f"attempt {index}: no token; MTCaptcha reships a new word")
            await asyncio.sleep(1.6)
            state = await read_mt_state(page)

        return MtCaptchaResult(False, None, sitekey, tuple(attempts), _ms(started))
    except Exception as exc:  # pragma: no cover - live browser path
        return MtCaptchaResult(
            False, None, None, tuple(attempts), _ms(started), error=f"{type(exc).__name__}: {exc}"
        )


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
