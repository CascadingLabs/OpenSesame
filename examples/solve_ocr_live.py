#!/usr/bin/env python3
"""Live OCR captcha solve through the OpenSesame public API.

Self-contained and deterministic: it generates a distorted-text captcha with a
known answer, serves a tiny *validating* page (file://), then drives it through
the real API — `Solver` → OCR `DirectAnswerEngine` → answer typed into the page
— and checks the page's verdict. OCR is imperfect per attempt, so it refreshes
the captcha and retries (exactly how a real OCR solver reaches a high pass rate).

Run (needs the `live` extra + Tesseract; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_ocr_live.py

The OCR "model" here is Tesseract (a real engine) wrapped as a registry
TextReader provider — the same seam a trained captcha model plugs into.
"""

from __future__ import annotations

import asyncio
import base64
import os
import random
import string
import subprocess
import sys
import tempfile
from pathlib import Path

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver
from OpenSesame.api.registry import ModelKey

SAFE = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no lookalike chars (I/L/O/0/1)
FONTS = (
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


# -- a mild distorted-text captcha (known answer) -------------------------

def render_captcha(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((f for f in FONTS if os.path.exists(f)), None)
    if font_path is None:
        raise RuntimeError("no TrueType font found; install liberation-fonts or dejavu")
    font = ImageFont.truetype(font_path, 38)
    img = Image.new("RGB", (200, 70), "white")
    draw = ImageDraw.Draw(img)
    x = 14
    for ch in text:
        cell = Image.new("RGBA", (40, 50), (0, 0, 0, 0))
        ImageDraw.Draw(cell).text((6, 2), ch, font=font, fill=(20, 20, 90, 255))
        cell = cell.rotate(random.uniform(-12, 12), expand=1, resample=Image.BICUBIC)
        img.paste(cell, (x, 8), cell)
        x += 34
    for _ in range(180):  # light speckle
        draw.point((random.randint(0, 199), random.randint(0, 69)), fill=(random.randint(140, 200),) * 3)
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def write_page(html_path: Path, png: bytes, answer: str) -> None:
    data_uri = "data:image/png;base64," + base64.b64encode(png).decode()
    html_path.write_text(
        f"""<!doctype html><html><body>
<h3>OCR captcha demo</h3>
<img id="captcha" src="{data_uri}" width="200" height="70">
<div><input id="answer" autocomplete="off">
<button id="submit" onclick="check()">Submit</button></div>
<div id="result"></div>
<script>
const ANSWER = {answer!r};
function check() {{
  const v = (document.getElementById('answer').value || '').trim().toUpperCase();
  document.getElementById('result').textContent =
    (v === ANSWER) ? 'Captcha is passed successfully!' : 'Incorrect CAPTCHA';
}}
</script></body></html>""",
        encoding="utf-8",
    )


# -- OCR provider: Tesseract as a registry TextReader ---------------------

class TesseractReader:
    """A :class:`~OpenSesame.api.providers.TextReader` backed by Tesseract."""

    model_id = "tesseract"
    device = "cpu"

    def read_text(self, image_path: str) -> tuple[str, float]:
        out = subprocess.run(
            ["tesseract", image_path, "stdout", "--psm", "8",
             "-c", f"tessedit_char_whitelist={SAFE}"],
            capture_output=True, text=True,
        ).stdout
        text = "".join(c for c in out.strip().upper() if c in SAFE)
        # Tesseract has no real confidence here; treat any 5-char read as usable.
        return text, 1.0 if len(text) == 5 else 0.3


# -- the live solve loop --------------------------------------------------

async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=[""],                 # file:// pages have an empty host
        models={"ocr": "tesseract"},
        min_confidence=0.5,               # ignore partial reads, refresh instead
    ))
    solver.registry.register_factory("ocr", lambda key: TesseractReader())

    work = Path(tempfile.mkdtemp(prefix="os-ocr-"))
    page_path = work / "captcha.html"
    max_attempts = 15

    async with BrowserSession(BrowserConfig(headless=True, stealth=True)) as browser:
        page = await browser.new_page("about:blank")
        async with solver.engine():       # warm the OCR provider once
            for attempt in range(1, max_attempts + 1):
                answer = "".join(random.choices(SAFE, k=5))
                write_page(page_path, render_captcha(answer), answer)
                await page.goto(f"file://{page_path}", timeout=15)

                challenge = Challenge.ocr(
                    url=f"file://{page_path}",
                    image_selector="#captcha",
                    response_field_selector="#answer",
                    capture_to=str(work / "cap.png"),
                )
                result = await solver.solve(challenge, page=page)

                read = result.answer or ""
                if not result.ok:
                    print(f"  attempt {attempt}: read low-confidence, refreshing")
                    continue

                # The Solver already typed the answer (apply=True). Submit + check.
                await page.eval_js("document.getElementById('submit').click()")
                verdict = str(await page.eval_js("document.getElementById('result').textContent") or "")
                hit = "passed" in verdict.lower()
                print(f"  attempt {attempt}: truth={answer} ocr={read} -> {verdict!r}")
                if hit:
                    print(f"\n✓ PASSED on attempt {attempt} "
                          f"(applied={result.applied}, {result.timing.elapsed_ms:.0f}ms solve)")
                    return 0

    print("\n✗ did not pass within attempt budget")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
