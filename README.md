<p align="center">
  <a href="https://github.com/CascadingLabs/OpenSesame">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="media/logo-dark.svg">
      <source media="(prefers-color-scheme: light)" srcset="media/logo-light.svg">
      <img src="media/logo-dark.svg" alt="OpenSesame" width="200">
    </picture>
  </a>
</p>

<p align="center">
  <a href="https://discord.gg/c8MKEaWEEK"><img src="https://img.shields.io/badge/Discord-Join-9af5bf?labelColor=071711&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-9af5bf?labelColor=071711" alt="License"></a>
</p>

# OpenSesame

Async-native self-hosted captcha/token-solving microservice with no paid
solver APIs.

## Usage

OCR fast-path smoke:

```bash
PYTHONPATH=src python - <<'PY'
from open_sesame.solvers.ocr import normalize_ocr_text

print(normalize_ocr_text(" A8 b-2 "))
PY
```

## Development

```bash
PYTHONPATH=src python -m pytest
```

The first OCR slice tracks `CAS-170`: normal distorted-text captchas where the
answer is the text in the image. The current implementation provides the
Tesseract fast-path contract and target registry; CRNN training is a later
slice.

Live 2Captcha smoke:

```bash
PYTHONPATH=src python examples/live_2captcha_normal.py --mode oracle
PYTHONPATH=src /home/andrew/Desktop/cl/VoidCrawl/.venv/bin/python examples/live_2captcha_normal.py --mode ocr --solver local-ml --model grafj-crnn-base --cache-dir .local/hf --local-files-only --allow-remote-code --ml-python python
```

The live harness uses `VoidCrawl` for stealth browser/session work and `httpx`
for async HTTP fetches.

Local downloadable OCR models:

```bash
PYTHONPATH=src python examples/local_ocr_model.py --list
PYTHONPATH=src python examples/local_ocr_model.py --model grafj-crnn-base --cache-dir .local/hf --allow-remote-code --download
PYTHONPATH=src python examples/benchmark_ocr_model.py /tmp/opensesame-2captcha-sample.jpg --model grafj-crnn-base --cache-dir .local/hf --allow-remote-code --json
PYTHONPATH=src python examples/live_2captcha_ocr_fetch.py --model grafj-crnn-base --cache-dir .local/hf --local-files-only --allow-remote-code
```

Benchmark output includes model load time, first inference time, warm latency,
RSS memory, CPU load, and GPU metrics when the selected device resolves to a GPU.
The Graf-J models use pinned Hugging Face revisions with custom model code, so
local execution requires explicit `--allow-remote-code`.

Labeled corpus eval:

```bash
PYTHONPATH=src python examples/eval_ocr_corpus.py path/to/corpus.jsonl --solver local-ml --model grafj-crnn-base --cache-dir .local/hf --local-files-only --allow-remote-code --json
```

Image classification experiments:

```bash
PYTHONPATH=src python examples/classify_image.py image.jpg --labels "bus,crosswalk,traffic light" --model openai/clip-vit-base-patch32
PYTHONPATH=src python examples/classify_image_grid.py grid.jpg --rows 3 --cols 3 --labels "bus,crosswalk,traffic light"
```

Fortress anti-bot throughput probe:

```bash
PYTHONPATH=src python examples/throughput_fortress.py --attempts 25 --concurrency 5
PYTHONPATH=src python examples/fortress_gauntlet.py --engine httpx --max-pages 10 --json
PYTHONPATH=/home/andrew/Desktop/cl/OpenSesame/src:/home/andrew/Desktop/cl/Yosoi uv run python /home/andrew/Desktop/cl/OpenSesame/examples/fortress_gauntlet.py --engine yosoi-auto --yosoi-path /home/andrew/Desktop/cl/Yosoi --json
```

## Test targets

See [docs/ocr-test-sites.md](docs/ocr-test-sites.md) for live held-out targets
and self-hosted/synthetic sources. See
[docs/throughput-targets.md](docs/throughput-targets.md) for anti-bot routing
throughput targets.

## Related projects

| Project        | Repo                                                                     |
|----------------|--------------------------------------------------------------------------|
| Cascading Labs | [github.com/CascadingLabs](https://github.com/CascadingLabs)             |
| Assets         | [github.com/CascadingLabs/Assets](https://github.com/CascadingLabs/Assets) |
| VoidCrawl      | [github.com/CascadingLabs/VoidCrawl](https://github.com/CascadingLabs/VoidCrawl) |
| Yosoi          | [github.com/CascadingLabs/Yosoi](https://github.com/CascadingLabs/Yosoi) |

## Community

- **Discord:** [discord.gg/c8MKEaWEEK](https://discord.gg/c8MKEaWEEK)
- **Support:** see [SUPPORT.md](SUPPORT.md)
- **Security:** see [SECURITY.md](SECURITY.md)
- **Code of Conduct:** see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## Contact

[contact@cascadinglabs.com](mailto:contact@cascadinglabs.com)

## License

Apache 2.0 — see [LICENSE](LICENSE).
