#!/usr/bin/env python3
"""Live smoke for the 2Captcha normal captcha demo.

Modes:
  ocr    - submit the current OpenSesame OCR candidate.
  oracle - submit the answer documented on the demo page.
  manual - submit --answer.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import httpx
from voidcrawl import BrowserConfig, BrowserSession

from open_sesame.harness.process import (
    parse_float_key_value_output,
    parse_key_value_output,
)
from open_sesame.harness.twocaptcha import parse_demo_expected_answer
from open_sesame.solvers.ocr import TesseractOCRSolver

TARGET_URL = "https://2captcha.com/demo/normal"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["ocr", "oracle", "manual"], default="ocr")
    parser.add_argument("--solver", choices=["tesseract", "local-ml"], default="tesseract")
    parser.add_argument("--model", default="grafj-crnn-base")
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/hf"))
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--allow-remote-code", action="store_true")
    parser.add_argument(
        "--ml-python",
        help="Python executable for local ML OCR when browser deps live elsewhere.",
    )
    parser.add_argument("--answer", help="Answer to submit in manual mode.")
    parser.add_argument(
        "--screenshot",
        default="/tmp/opensesame-2captcha-normal.png",
        help="Where to write the final browser screenshot.",
    )
    args = parser.parse_args()

    if args.mode == "manual" and not args.answer:
        parser.error("--answer is required in manual mode")

    passed = asyncio.run(
        run_smoke(
            args.mode,
            args.answer,
            args.screenshot,
            solver=args.solver,
            model=args.model,
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
            allow_remote_code=args.allow_remote_code,
            ml_python=args.ml_python,
        )
    )
    if not passed:
        raise SystemExit(1)


async def run_smoke(
    mode: str,
    manual_answer: str | None,
    screenshot: str,
    *,
    solver: str,
    model: str,
    cache_dir: Path,
    local_files_only: bool,
    allow_remote_code: bool,
    ml_python: str | None,
) -> bool:
    async with BrowserSession(
        BrowserConfig(
            headless=True,
            stealth=True,
            chrome_executable="/usr/bin/chromium",
            extra_args=["--window-size=1280,900"],
        )
    ) as browser:
        page = await asyncio.wait_for(browser.new_page(TARGET_URL), timeout=45)
        try:
            await asyncio.wait_for(page.wait_for_network_idle(timeout=8), timeout=10)
        except TimeoutError:
            pass

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            answer = await choose_answer(
                page,
                client,
                mode,
                manual_answer,
                solver=solver,
                model=model,
                cache_dir=cache_dir,
                local_files_only=local_files_only,
                allow_remote_code=allow_remote_code,
                ml_python=ml_python,
            )

        await page.type_into("#simple-captcha-field", answer)
        await page.evaluate_js(
            "document.querySelector('#simple-captcha-field').closest('form').requestSubmit()"
        )
        await asyncio.sleep(1)

        body_text = await page.eval_js("document.body.innerText")
        passed = "Captcha is passed successfully!" in str(body_text)
        await page.screenshot(path=screenshot)

    print(f"mode={mode}")
    print(f"answer={answer}")
    print(f"passed={passed}")
    print(f"screenshot={screenshot}")
    return passed


async def choose_answer(
    page: object,
    client: httpx.AsyncClient,
    mode: str,
    manual_answer: str | None,
    *,
    solver: str,
    model: str,
    cache_dir: Path,
    local_files_only: bool,
    allow_remote_code: bool,
    ml_python: str | None,
) -> str:
    if mode == "manual":
        assert manual_answer is not None
        return manual_answer

    if mode == "oracle":
        body_text = await page.eval_js("document.body.innerText")
        answer = parse_demo_expected_answer(str(body_text))
        if answer is None:
            raise RuntimeError("Could not find documented 2Captcha demo answer")
        return answer

    return await solve_current_image_with_ocr(
        page,
        client,
        solver=solver,
        model=model,
        cache_dir=cache_dir,
        local_files_only=local_files_only,
        allow_remote_code=allow_remote_code,
        ml_python=ml_python,
    )


async def solve_current_image_with_ocr(
    page: object,
    client: httpx.AsyncClient,
    *,
    solver: str,
    model: str,
    cache_dir: Path,
    local_files_only: bool,
    allow_remote_code: bool,
    ml_python: str | None,
) -> str:
    image_src = await page.eval_js(
        "document.querySelector('img[alt=\"normal captcha example\"]')?.src"
    )
    if image_src is None:
        raise RuntimeError("Could not find normal captcha image")

    image_url = urljoin(TARGET_URL, str(image_src))

    image_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            image_path = Path(tmp.name)
            response = await client.get(image_url)
            response.raise_for_status()
            tmp.write(response.content)

        result = solve_image(
            image_path,
            solver,
            model,
            cache_dir,
            local_files_only,
            allow_remote_code,
            ml_python,
        )
    finally:
        if image_path is not None:
            try:
                os.unlink(image_path)
            except OSError:
                pass

    if result.best is None:
        raise RuntimeError("OCR solver returned no answer candidate")
    print(f"ocr_solver={solver}")
    print(f"ocr_model={model if solver == 'local-ml' else 'tesseract'}")
    print(f"ocr_confidence={result.best.confidence:.3f}")
    print(f"ocr_raw={result.best.raw_text}")
    return result.best.text


def solve_image(
    image_path: Path,
    solver: str,
    model: str,
    cache_dir: Path,
    local_files_only: bool,
    allow_remote_code: bool,
    ml_python: str | None,
):
    if solver == "local-ml":
        if ml_python is not None:
            return solve_image_with_ml_subprocess(
                image_path,
                ml_python,
                model,
                cache_dir,
                local_files_only,
                allow_remote_code,
            )
        from open_sesame.solvers.local_ml import LocalMLCaptchaOCRSolver
        from open_sesame.solvers.ml_config import LocalOCRConfig

        return LocalMLCaptchaOCRSolver(
            LocalOCRConfig(
                model_id=model,
                cache_dir=cache_dir,
                local_files_only=local_files_only,
                allow_remote_code=allow_remote_code,
            )
        ).solve_image(image_path)

    if solver == "tesseract":
        return TesseractOCRSolver().solve_image(image_path)

    msg = f"unknown solver {solver!r}"
    raise ValueError(msg)


def solve_image_with_ml_subprocess(
    image_path: Path,
    ml_python: str,
    model: str,
    cache_dir: Path,
    local_files_only: bool,
    allow_remote_code: bool,
):
    command = [
        ml_python,
        str(Path(__file__).with_name("local_ocr_model.py")),
        str(image_path),
        "--model",
        model,
        "--cache-dir",
        str(cache_dir),
    ]
    if local_files_only:
        command.append("--local-files-only")
    if allow_remote_code:
        command.append("--allow-remote-code")

    env = os.environ.copy()
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"
    )
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    answer = parse_key_value_output(completed.stdout, "answer")
    confidence = parse_float_key_value_output(completed.stdout, "confidence")
    if answer is None:
        msg = f"local ML subprocess returned no answer:\n{completed.stdout}"
        raise RuntimeError(msg)

    from open_sesame.contracts import CandidateAnswer, SolveResult

    return SolveResult(
        kind="answer",
        solver="local-ml-subprocess",
        candidates=(
            CandidateAnswer(
                text=answer,
                confidence=confidence,
                source=f"local-ml-subprocess:{model}",
                raw_text=answer,
                metadata={"stdout": completed.stdout},
            ),
        ),
        metadata={"command": command, "stdout": completed.stdout},
    )


if __name__ == "__main__":
    main()
