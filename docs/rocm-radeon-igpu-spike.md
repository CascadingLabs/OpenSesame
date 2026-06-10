# ROCm Radeon iGPU Spike Notes

These notes capture the ROCm path for OpenSesame local model acceleration.

## Current machine facts

Initial state observed on 2026-06-09:

- OS: Arch Linux, kernel `7.0.11-arch1-1`.
- CPU/APU: `AMD Ryzen AI 9 HX 370 w/ Radeon 890M`.
- GPU: PCI device `c2:00.0`, `AMD/ATI Strix [Radeon 880M / 890M]`, using the
  `amdgpu` kernel driver.
- NPU: PCI device `c3:00.1`, `AMD Strix/Krackan/Strix Halo Neural Processing
  Unit`, using `amdxdna`.
- User groups: `andrew` was not in `video` or `render` during the check.
- ROCm tools were not installed (`rocminfo`, `rocm-smi`, `amd-smi`, `hipcc`,
  and `/opt/rocm` were absent).
- Active Python imported PyTorch `2.9.1+cu128`, a CUDA build rather than a ROCm
  build; `torch.version.hip` was `null` and `torch.cuda.is_available()` was
  `False`.
- Arch had ROCm packages available in the package DB:
  `rocm-core`, `hip-runtime-amd`, `rocminfo`, `rocm-smi-lib`,
  `python-pytorch-rocm`, and `python-pytorch-opt-rocm`.

Post-install state observed after restart:

- ROCm packages installed: `rocm-core 7.2.4-1`, `hip-runtime-amd 7.2.4-1`,
  `rocminfo 7.2.4-1`, `rocm-smi-lib 7.2.0-2`, and
  `python-pytorch-opt-rocm 2.12.0-3`.
- ROCm tools are installed under `/opt/rocm/bin`.
- Real device nodes exist: `/dev/kfd`, `/dev/dri/renderD128`, and
  `/dev/dri/card1`.
- `andrew` is listed in `render` and `video` in the group database.
- `rocminfo` sees the GPU agent as `gfx1150`, marketing name
  `AMD Radeon 890M Graphics`, with 16 compute units and APU memory properties.
- `/usr/bin/python` imports ROCm PyTorch `2.12.0` with HIP `7.2.53211`;
  `torch.cuda.is_available()` is `True`, `torch.cuda.device_count()` is `1`,
  and a small GPU tensor matmul succeeds.
- OpenSesame's device resolver works under `/usr/bin/python`:
  `resolve_torch_device_info("auto")` returns `torch_device="cuda:0"`,
  `pipeline_device=0`, `accelerator="rocm"`, and
  `device_name="AMD Radeon 890M Graphics"`.
- The default `python` in the Codex shell is still the mise Python with CUDA
  PyTorch `2.9.1+cu128`; that environment does not see ROCm and resolves
  OpenSesame `device=auto` to CPU.

The Codex sandbox can still obscure `/dev/kfd` and `/dev/dri/*` unless the check
runs outside the filesystem sandbox. Prefer a normal terminal or an escalated
tool check for final GPU-access validation.

## External context

- AMD ROCm release history listed ROCm `7.2.4` as released on 2026-05-29.
- AMD Radeon/Ryzen ROCm docs describe ROCm 7.2.x PyTorch support for Ryzen APUs,
  including Ryzen AI 300-family APUs.
- AMD's formal Linux support matrix is Ubuntu/RHEL/SLES/Debian/Rocky/Oracle
  oriented; Arch is a community path. Treat this as a spike until proven stable.

Useful docs:

- https://rocm.docs.amd.com/en/develop/release/versions.html
- https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/
- https://rocm.docs.amd.com/projects/install-on-linux/en/latest/reference/system-requirements.html
- https://rocm.docs.amd.com/en/develop/compatibility/ml-compatibility/pytorch-compatibility.html

## What ROCm could unlock for OpenSesame

ROCm is mainly useful here as a speed-up path for local model inference. It does
not solve browser-bound token minting, Turnstile/DataDome/reCAPTCHA v3
clearance, or VoidCrawl session invariants.

Likely useful OpenSesame workloads:

- `grafj-conv-transformer-base`: default local captcha recognizer. CPU may
  already be fast, so this is the regression baseline rather than the strongest
  expected win.
- `grafj-crnn-base`: second baseline for `CAS-170` normal distorted-text OCR.
- `anuashok-trocr-v3`: heavier TrOCR fallback candidate and more likely to
  benefit from GPU acceleration.
- CLIP-style tile classifiers in `examples/classify_image.py` and
  `examples/classify_image_grid.py`, especially when scoring multiple
  reCAPTCHA/hCaptcha crops and labels.
- Future stronger OCR or image-classification models that are too slow on CPU
  but fit within the iGPU's shared-memory constraints.

Expectations:

- Small OCR models might not beat CPU once transfer overhead is included.
- Medium OCR, TrOCR, and CLIP grid classification are better candidates.
- Large VLMs or LLMs might fit only when quantized, and could still be slow or
  finicky on an iGPU.
- Tiny fine-tunes may be possible, but inference should be proven first.

## OpenSesame runtime behavior

OpenSesame treats ROCm as a PyTorch GPU backend. ROCm PyTorch exposes AMD GPUs
through the `torch.cuda` API, so `--device auto` resolves to `cuda:0` when ROCm
is visible. Diagnostic metadata records `accelerator=rocm`, `hip_version`, and
the detected device name.

Current ROCm-aware paths:

- `examples/benchmark_ocr_model.py --device auto`
- `examples/classify_image.py --device auto`
- `examples/classify_image_grid.py --device auto`
- `examples/replay_recaptcha_failures.py --device auto`
- `examples/google_recaptcha_v2_actor.py --device auto`

Use `--device cpu` to force CPU fallback and `--device cuda:0` to force the
first ROCm/CUDA-visible device.

## Restart-window install plan

Run this only when a reboot is acceptable.

1. Refresh the system.

   ```bash
   sudo pacman -Syu
   ```

2. Install ROCm runtime, diagnostics, and ROCm PyTorch.

   ```bash
   sudo pacman -S rocm-core hip-runtime-amd rocminfo rocm-smi-lib python-pytorch-opt-rocm
   ```

   If `python-pytorch-opt-rocm` conflicts or is unavailable, use
   `python-pytorch-rocm` instead.

3. Add the user to GPU access groups.

   ```bash
   sudo usermod -aG render,video "$USER"
   ```

4. Reboot.

   ```bash
   systemctl reboot
   ```

## Post-restart validation

After logging back in:

```bash
id
ls -l /dev/kfd /dev/dri/renderD* /dev/dri/card*
rocminfo | rg -i 'Name:|gfx|Agent|Marketing|Radeon|Strix'
python - <<'PY'
import torch
print(torch.__version__)
print(torch.version.hip)
print(torch.cuda.is_available())
print(torch.cuda.device_count())
for i in range(torch.cuda.device_count()):
    print(i, torch.cuda.get_device_name(i))
PY
```

The pass condition for ROCm runtime visibility is:

- `/dev/kfd` exists.
- A `/dev/dri/renderD*` node exists.
- `id` includes `render` and ideally `video`.
- `rocminfo` sees a GPU agent.
- PyTorch reports `torch.cuda.is_available() == True`; ROCm PyTorch still uses
  the `torch.cuda` API name.

This condition is satisfied for `/usr/bin/python` on the current machine. It is
not satisfied for the mise-managed default `python`.

## Python environment choice

Use system Python for ROCm tests unless the active virtualenv/mise environment is
rebuilt around ROCm PyTorch:

```bash
PYTHONPATH=src /usr/bin/python - <<'PY'
from open_sesame.solvers.ml_config import resolve_torch_device
print(resolve_torch_device("auto"))
PY
```

Expected result after the successful ROCm install:

```text
('cuda:0', 0)
```

Using bare `python` currently hits the mise environment and remains CPU-only.

Verified OpenSesame ROCm smoke:

```bash
PYTHONPATH=src /usr/bin/python - <<'PY'
import torch
from open_sesame.solvers.ml_config import resolve_torch_device_info

info = resolve_torch_device_info("auto")
print(info.as_dict())
assert info.torch_device == "cuda:0"
assert info.pipeline_device == 0
assert info.accelerator == "rocm"
assert info.hip_version
assert info.device_name == "AMD Radeon 890M Graphics"

x = torch.arange(16 * 16, device=info.torch_device, dtype=torch.float32).reshape(16, 16)
y = x @ x.T
torch.cuda.synchronize()
print(str(y.device), tuple(y.shape), float(y.sum().cpu()))
PY
```

This passed with `tensor_device=cuda:0` and HIP `7.2.53211`.

The ROCm Python does not currently have every Hugging Face benchmark dependency:
`transformers`, `huggingface_hub`, and `psutil` were missing under
`/usr/bin/python`. Install those before running the OCR/CLIP benchmark examples
under ROCm.

## Live ROCm eval result

An ignored UV venv was created at `.local/venvs/rocm` using system ROCm PyTorch:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools \
  uv venv --python /usr/bin/python --system-site-packages .local/venvs/rocm

UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools \
  uv pip install --python .local/venvs/rocm/bin/python -e '.[ml-base]'

UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools \
  uv pip install --python .local/venvs/rocm/bin/python 'transformers>=4.57,<5'
```

The final pin matters: the cached Graf-J custom OCR pipelines did not run under
`transformers 5.10.2`, while `transformers 4.57.6` matches the project's declared
range. The Graf-J custom pipeline still failed because its custom pipeline
processor was `None`; keep that as a separate compatibility bug. The heavier
TrOCR model worked on ROCm.

ROCm venv check:

```bash
PYTHONPATH=src .local/venvs/rocm/bin/python - <<'PY'
import torch, transformers
from open_sesame.solvers.ml_config import resolve_torch_device_info
print(torch.__version__, torch.version.hip, torch.cuda.get_device_name(0))
print(transformers.__version__)
print(resolve_torch_device_info("auto").as_dict())
PY
```

Observed:

- `torch 2.12.0`, HIP `7.2.53211`
- `transformers 4.57.6`
- `device_name="AMD Radeon 890M Graphics"`
- `accelerator="rocm"`, `torch_device="cuda:0"`

Live HTTP OCR fetch passed with TrOCR on ROCm:

```bash
PYTHONPATH=src .local/venvs/rocm/bin/python examples/live_2captcha_ocr_fetch.py \
  --model anuashok-trocr-v3 \
  --cache-dir .local/hf \
  --local-files-only \
  --device auto \
  --save-image /tmp/opensesame-2captcha-rocm-live-trocr.jpg
```

Observed:

```text
target=https://2captcha.com/demo/normal
model=anuashok-trocr-v3
device=cuda:0
expected=W9H5K
answer=W9H5K
passed=True
elapsed_ms=4148.4
```

Full live browser submit also passed. VoidCrawl drove the page, the ROCm venv
performed OCR in the subprocess, and the live form accepted the answer:

```bash
PYTHONPATH=src:/home/andrew/Desktop/cl/VoidCrawl \
  /home/andrew/Desktop/cl/VoidCrawl/.venv/bin/python examples/live_2captcha_normal.py \
  --mode ocr \
  --solver local-ml \
  --model anuashok-trocr-v3 \
  --cache-dir .local/hf \
  --local-files-only \
  --ml-python .local/venvs/rocm/bin/python \
  --screenshot /tmp/opensesame-2captcha-rocm-submit.png
```

Observed:

```text
ocr_solver=local-ml
ocr_model=anuashok-trocr-v3
ocr_raw=W9H5K
mode=ocr
answer=W9H5K
passed=True
screenshot=/tmp/opensesame-2captcha-rocm-submit.png
```

The reCAPTCHA augmentation replay path also works with ROCm. This is not a live
submit, but it verifies the `--augmentations helpful` tool belt against cached
failure crops using CLIP on the Radeon:

```bash
PYTHONPATH=src .local/venvs/rocm/bin/python examples/replay_recaptcha_failures.py \
  --limit 1 \
  --augmentations helpful \
  --device auto \
  --cache-dir .local/hf \
  --local-files-only \
  --ml-python .local/venvs/rocm/bin/python
```

Observed on the first cached failure example:

- target label: `bus`
- augmentation preset: `helpful`
- model: `openai/clip-vit-base-patch32`
- planned two tiles with `consensus=1.0`
- each planned tile had five target votes across identity, contrast, sharpness,
  brightness, and center-crop variants.

## Live image-grid CLIP result

The imageful reCAPTCHA v2 actor can target a direct URL and run CLIP tile
planning on ROCm:

```bash
PYTHONPATH=src:/home/andrew/Desktop/cl/VoidCrawl \
  /home/andrew/Desktop/cl/VoidCrawl/.venv/bin/python examples/google_recaptcha_v2_actor.py \
  --url https://2captcha.com/demo/recaptcha-v2 \
  --run-timeout 120 \
  --timeout 30 \
  --wait-secs 20 \
  --target-label auto \
  --models openai/clip-vit-base-patch32 \
  --device auto \
  --augmentations helpful \
  --cache-dir .local/hf \
  --local-files-only \
  --ml-python .local/venvs/rocm/bin/python \
  --max-rounds 2 \
  --post-verify-wait 3 \
  --audit-dir .local/recaptcha-runs \
  --screenshot .local/recaptcha/2captcha-recaptcha-live-rocm.png \
  --challenge-image .local/recaptcha/2captcha-recaptcha-live-rocm-challenge.png \
  --prompt-image .local/recaptcha/2captcha-recaptcha-live-rocm-prompt.png \
  --post-verify-screenshot .local/recaptcha/2captcha-recaptcha-live-rocm-post-verify.png
```

Observed:

- Target page: `https://2captcha.com/demo/recaptcha-v2`
- Live captcha kind: `recaptcha`
- Challenge frame visible: yes
- Prompt OCR: `Select all squares with crosswalks`
- Target label: `crosswalks`
- Grid: 4x4
- Model: `openai/clip-vit-base-patch32`
- Device requested by actor: `auto`; ROCm subprocess resolves this to `cuda:0`
- Augmentations: `helpful`
- Round 1: 16 active tiles, 8 planned/clicked tiles
- Round 2: 8 active tiles, 0 planned/clicked tiles
- Verify clicked: yes
- Token present after verify: no
- Audit record:
  `.local/recaptcha-runs/20260609T103051.738831Z-failure/metadata.json`

A stricter threshold run also worked mechanically but produced no tile plan for a
`motorcycles` prompt:

```bash
--min-target-score 0.55 --min-score-margin 0.20
```

Observed:

- Target label: `motorcycles`
- Round 1: 16 active tiles, 0 planned/clicked tiles
- Verify clicked: yes
- Token present after verify: no
- Audit record:
  `.local/recaptcha-runs/20260609T103223.343340Z-failure/metadata.json`

Interpretation: the live imageful CLIP pipeline is working: browser challenge
capture, prompt OCR, grid crop, tile split, ROCm CLIP scoring, helpful
augmentations, planned clicks, and verify click all execute against a real
reCAPTCHA v2 page. It is not yet a reliable solver. `openai/clip-vit-base-patch32`
is too weak/noisy for this challenge family by itself, so the next model step is
trying stronger cached/downloaded vision models or a detector trained for
reCAPTCHA tile classes.

## Benchmark sequence

Use the same sample image for CPU and ROCm runs.

```bash
PYTHONPATH=src python examples/benchmark_ocr_model.py /tmp/opensesame-2captcha-sample.jpg \
  --model grafj-crnn-base \
  --cache-dir .local/hf \
  --local-files-only \
  --allow-remote-code \
  --device cpu \
  --json
```

Then run the same model with automatic device resolution:

```bash
PYTHONPATH=src python examples/benchmark_ocr_model.py /tmp/opensesame-2captcha-sample.jpg \
  --model grafj-crnn-base \
  --cache-dir .local/hf \
  --local-files-only \
  --allow-remote-code \
  --device auto \
  --json
```

If `auto` still resolves to CPU after ROCm is installed, collect:

```bash
python - <<'PY'
import torch
print(torch.__version__)
print(torch.version.hip)
print(torch.cuda.is_available())
print(torch.cuda.device_count())
PY
rocminfo | head -80
```

For reCAPTCHA replay, compare CPU and ROCm against the same saved failure:

```bash
PYTHONPATH=src python examples/replay_recaptcha_failures.py \
  --example-id 20260609T014954.899104Z \
  --models openai/clip-vit-large-patch14 \
  --device cpu \
  --cache-dir .local/hf \
  --local-files-only \
  --ml-python python

PYTHONPATH=src python examples/replay_recaptcha_failures.py \
  --example-id 20260609T014954.899104Z \
  --models openai/clip-vit-large-patch14 \
  --device auto \
  --cache-dir .local/hf \
  --local-files-only \
  --ml-python python
```

## Stop criteria

Stop and reassess before changing system config further if:

- `rocminfo` cannot see the iGPU after package install, group membership, and a
  reboot.
- PyTorch cannot see ROCm but `rocminfo` can; that points to the PyTorch package
  path, not the kernel/runtime path.
- The small OCR models get slower on GPU due to transfer overhead. In that case,
  reserve ROCm for heavier TrOCR/CLIP/tile-classification workloads.
