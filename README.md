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
  <a href="https://discord.gg/UnqRNzFYjM"><img src="https://img.shields.io/badge/Discord-Join-9af5bf?labelColor=071711&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-9af5bf?labelColor=071711" alt="License"></a>
</p>

# OpenSesame

Self-hosted captcha/token-solving microservice with no paid solver APIs.

## Usage

OpenSesame is the **solver**, not the browser. [VoidCrawl](https://github.com/CascadingLabs/VoidCrawl)
detects the wall and hands over a descriptor; OpenSesame drives the live page with
**local models** (DOM-driven, no point-and-click) and, by default, **resolves the
solution into the page itself** — token injected / answer typed. Callers just
check `result.ok`; no inject step.

```python
from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver

# Policy is data. allow_sites is default-deny (empty = solve nothing).
solver = default_solver(SolverPolicy.auto_only(allow_sites=["www.google.com"]))

captcha = await page.capture_captcha()             # VoidCrawl describes the wall
result = await solver.solve(Challenge.from_capture(captcha), page=page)

if result.ok:        # the page now carries the token — submit the form / continue
    ...
```

The model loads once on first use and stays cached — no warmup ceremony
(`async with solver.engine():` is optional, pre-warming from policy + freeing
VRAM on exit). Failure is a value (`result.status`), never an exception — except
a denied site (`SiteNotAllowed`).

**Auto-apply vs over-the-wire.** By default OpenSesame applies the solution to the
live page (it already drives it). For the narrower over-the-wire case — relaying a
token to a different session — set policy `apply = false`; then `result.token` /
`result.answer` carry the raw solution and you inject it yourself
(`await other_page.inject_captcha_token(result.token)`).

The async ticket API (`submit` → `await_result`) is the same seam a future
Redis/noVNC deployment swaps behind. See
[`examples/solve_with_api.py`](examples/solve_with_api.py) and
[`opensesame.example.toml`](opensesame.example.toml).

v1 use cases: **reCAPTCHA v2 (audio side-door + image grid)**, **Cloudflare Turnstile**, and **OCR / distorted-text captchas**.

**Scope & generalization.** The architecture is vendor-agnostic (`Family`→engine
routing, the audio/grid strategy composite, the provider registry), and the
reCAPTCHA engines cover **v2 + Enterprise**, both same-origin and **cross-origin**.
They drive the challenge through VoidCrawl 0.3.5's frame-scoped eval
(`eval_js_in_frame`), so they work on Google's own `api2/demo` **and** on real
third-party sites where the reCAPTCHA frames are served cross-origin from
`google.com`. Cross-origin needs the session launched with
`extra_args=["disable-site-isolation-trials"]` (Chrome keeps the google.com frames
in-process); without it the engine returns an actionable `FAILED` with
`metadata["frame_isolated"] = True`. **Cloudflare Turnstile** is also solved: it
has no puzzle, so OpenSesame locates the "Verify you are human" checkbox — which
lives in a closed shadow root inside the cross-origin `challenges.cloudflare.com`
frame — via VoidCrawl 0.3.6's accessibility locator (`ax_box_in_frame`), drives a
humanized compositor click, and harvests `cf-turnstile-response`. reCAPTCHA v3 /
hCaptcha remain detect-and-route (`REFUSED`, `route: anti-bot`), not solve targets.
Full write-up: [`docs/recaptcha-generalization.md`](docs/recaptcha-generalization.md).

### CLI

```bash
opensesame check                                   # validate policy + report engines/models
opensesame download audio  --model openai/whisper-base.en
opensesame download vision --model verytuffcat/recaptcha
opensesame download ocr    --model anuashok/ocr-captcha-v3
```

## Development

<!-- Clone, install, run tests. -->

```bash
PYTHONPATH=src python -m pytest      # unit tests (no browser, no models)
```

## Related projects

| Project        | Repo                                                                     |
|----------------|--------------------------------------------------------------------------|
| Cascading Labs | [github.com/CascadingLabs](https://github.com/CascadingLabs)             |
| Assets         | [github.com/CascadingLabs/Assets](https://github.com/CascadingLabs/Assets) |
| VoidCrawl      | [github.com/CascadingLabs/VoidCrawl](https://github.com/CascadingLabs/VoidCrawl) |
| Yosoi          | [github.com/CascadingLabs/Yosoi](https://github.com/CascadingLabs/Yosoi) |

## Community

- **Discord:** [discord.gg/UnqRNzFYjM](https://discord.gg/UnqRNzFYjM)
- **Support:** see [SUPPORT.md](SUPPORT.md)
- **Security:** see [SECURITY.md](SECURITY.md)
- **Code of Conduct:** see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## Contact

[contact@cascadinglabs.com](mailto:contact@cascadinglabs.com)

## License

Apache 2.0 — see [LICENSE](LICENSE).
