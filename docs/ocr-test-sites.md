# OCR Test Sites

OpenSesame `CAS-170` needs two categories of OCR targets:

- **Training/eval generators** with unlimited labels for regression runs.
- **Held-out live targets** whose generator was not used for training, so synthetic accuracy can be falsified early.

## Live Held-Out Candidates

| Target | URL | Use | Caveat |
|---|---|---|---|
| 2Captcha normal demo | https://2captcha.com/demo/normal | First live form smoke for normal distorted-text captchas. | Vendor demo page, so treat as an integration target rather than training data. |
| AZcaptcha image/text demo | https://azcaptcha.com/demo | Candidate image-to-text captcha page and upload flow. | Solving flow expects an API key; still useful for finding page/image shape. |
| CaptchaSonic normal captcha demo | https://captchasonic.com/en/demo/normal-captcha | Candidate normal-captcha demo route from their public demo listing. | May require extension/API key for full solve flow. |

## Self-Hosted / Synthetic Sources

| Source | URL | Use | Caveat |
|---|---|---|---|
| Securimage | https://github.com/dapphp/securimage | Self-hosted PHP generator for labeled normal OCR fixtures. | Should not be the only eval source. |
| Python `captcha` package | https://pypi.org/project/captcha/ | Synthetic local generator for quick labeled fixtures. | Synthetic-only success does not satisfy CAS-170. |
| Kaptcha | https://github.com/penggle/kaptcha | Java generator candidate for training diversity. | Needs local harness/container later. |

## Acceptance Notes

- Do not build one model per site.
- Start with Tesseract/OpenCV as a zero-training fast path.
- Route to a future generalist CRNN only when OCR confidence is below threshold.
- Report held-out accuracy separately from synthetic generator accuracy.

## Current 2Captcha Smoke Result

The static 2Captcha normal demo image currently reads visually as `W9H5K`.
Submitting that value clears the page with `Captcha is passed successfully!`.

Current live-smoke results:

| Mode | Answer | Result | Notes |
|---|---|---|---|
| `oracle` | `W9H5K` | Pass | Parses the demo page's documented answer token and proves the browser submit path clears the wall. |
| `ocr` | `NNeHR5EK` | Fail | Current Tesseract-only path misreads the red outline glyphs and reports no positive TSV confidence. |

This target is a useful failure case for the next OCR preprocessing/CRNN slice.

Run the live smoke:

```bash
PYTHONPATH=src python examples/live_2captcha_normal.py --mode oracle
PYTHONPATH=src python examples/live_2captcha_normal.py --mode ocr
PYTHONPATH=src python examples/live_2captcha_normal.py --mode ocr --solver local-ml --model grafj-crnn-base --cache-dir .local/hf --local-files-only --allow-remote-code
```

If browser and ML dependencies are split across Python environments, the live
browser smoke can let the VoidCrawl Python own browser automation and delegate
OCR to the ML Python:

```bash
PYTHONPATH=src /home/andrew/Desktop/cl/VoidCrawl/.venv/bin/python examples/live_2captcha_normal.py --mode ocr --solver local-ml --model grafj-crnn-base --cache-dir .local/hf --local-files-only --allow-remote-code --ml-python python
```

That subprocess path is for validation. The faster production shape is a single
runtime with VoidCrawl and ML dependencies installed together, or a persistent
local OCR worker that keeps the model loaded. Warm `grafj-crnn-base` CPU
inference is single-digit milliseconds; repeated cold subprocess loads are not.

The smoke is async-native: VoidCrawl owns the browser/session path and `httpx`
owns image fetches.

## Current Local ML OCR Result

Two downloaded Graf-J captcha-specific models solved the same held-out 2Captcha
sample as `W9H5K` on CPU:

| Model | Device | Load | First inference | Warm avg | Warm p95 | RSS peak | CPU avg | GPU |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `grafj-conv-transformer-base` | CPU | 1404 ms | 15.7 ms | 16.1 ms | 24.2 ms | 760.8 MB | 47.4% | unavailable |
| `grafj-crnn-base` | CPU | 1488 ms | 87.9 ms | 9.3 ms | 12.9 ms | 719.5 MB | 46.8% | unavailable |

Current environment note: this machine has an AMD Strix Radeon 880M/890M iGPU,
but the active Python stack is a CUDA PyTorch build with no visible CUDA/ROCm
device, and `rocminfo` is not installed. Benchmarks therefore ran on CPU. GPU
metrics are emitted only when the configured model device resolves to a GPU.

Benchmark locally:

```bash
PYTHONPATH=src python examples/benchmark_ocr_model.py /tmp/opensesame-2captcha-sample.jpg --model grafj-crnn-base --cache-dir .local/hf --allow-remote-code --json
```

The Graf-J entries are pinned to downloaded Hugging Face revisions and require
explicit `--allow-remote-code` because they use custom Transformers code.

Live HTTP-only OCR validation fetches the current 2Captcha demo page and image,
parses the documented expected answer, and compares the local model output:

```bash
PYTHONPATH=src python examples/live_2captcha_ocr_fetch.py --model grafj-crnn-base --cache-dir .local/hf --local-files-only --allow-remote-code
```

Current run against the live page passed:

| Solver | Expected | Answer | Result | Notes |
|---|---|---|---|---|
| `grafj-crnn-base` | `W9H5K` | `W9H5K` | Pass | Live HTTP fetch, CPU, local cached model. |
| `tesseract` | `W9H5K` | `NNeHR5EK` | Fail | Same fetched image via corpus eval. |

Corpus manifests are JSONL rows with at least `image` and `expected` fields:

```json
{"id":"2captcha-current","image":"images/2captcha-current.jpg","expected":"W9H5K","source":"2captcha"}
```

Run corpus eval:

```bash
PYTHONPATH=src python examples/eval_ocr_corpus.py path/to/corpus.jsonl --solver local-ml --model grafj-crnn-base --cache-dir .local/hf --local-files-only --allow-remote-code --json
```

AZcaptcha also exposes a no-key sample image endpoint at
`https://azcaptcha.com/api/v1/captcha`. One sampled image read visually as
`Q7CWF`; Tesseract produced `Q7CWF` but with no positive TSV confidence. No
public no-key verification endpoint has been found yet, so treat AZcaptcha as an
image-fetch sample until we add an API-key-backed solve/verify path.
