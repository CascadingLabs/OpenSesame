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

Self-hosted captcha/token-solving microservice with no paid solver APIs.

## Disclaimer & Responsible Use

[DISCLAIMER.md](DISCLAIMER.md)

## Usage

OpenSesame currently ships a local human-takeover control center for live
VoidCrawl challenge sessions.

```bash
uv run opensesame serve
# or open the browser automatically
uv run opensesame watch
```

The operator UI listens on `http://127.0.0.1:8765` by default and stores event
metadata in async SQLite at `.opensesame/opensesame.sqlite3`.

Create a takeover event from a VoidCrawl `capture_challenge` payload:

```bash
curl -X POST http://127.0.0.1:8765/api/takeovers \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo","event_id":"demo-1","captcha_kind":"turnstile","vnc_url":"vnc://127.0.0.1:5900","novnc_url":"http://127.0.0.1:6080"}'
```

Use native VNC for local operation and noVNC for remote/SSH operation.

Drive a real local demo with VoidCrawl:

```bash
# terminal 1: operator UI
uv run opensesame serve

# terminal 2: browser/noVNC, from ../VoidCrawl
./docker/run-headful.sh

# terminal 3: launch VoidCrawl to a demo site, send interrupt to OpenSesame,
# and wait for the UI resolution button
uv run opensesame demo cloudflare turnstile
uv run opensesame demo cloudflare managed
uv run opensesame demo recaptcha v2
uv run opensesame demo recaptcha v3-enterprise
```

Use `--all`/`-A` on the family commands to queue a focused stress-test set:

```bash
uv run opensesame demo cloudflare -A
uv run opensesame demo recaptcha -A
```

DataDome intentionally has no demo target yet; `opensesame demo datadome` reports
that it needs an owned or respectful fixture before it joins the MPP demo set.

Then open `http://127.0.0.1:8765`, click into VNC/noVNC, solve the challenge,
and press **Mark resolved** in OpenSesame. The demo command re-probes the same
VoidCrawl tab and prints whether the captcha is gone.

For the MTCaptcha HITL resume example:

```bash
# terminal 1, from ../VoidCrawl
./docker/run-headful.sh

# terminal 2, from this repo
uv run python examples/mtcaptcha_resume.py --open-ui
# equivalent CLI path:
uv run opensesame demo mtcaptcha --open-ui
```

This sends the same VoidCrawl tab to OpenSesame, lets a human clear the
MTCaptcha challenge in noVNC/VNC, then resumes automation and re-probes the page.

To queue the focused MPP demo set, reCAPTCHA plus Cloudflare, as pending work for
frontend stress testing without solving any of them:

```bash
# terminal 1, from ../VoidCrawl
./docker/run-headful.sh

# terminal 2, from this repo
uv run opensesame demo all
```

`all` opens concurrent tabs in one VoidCrawl browser session, queues the
resulting browser states in OpenSesame, and does not auto-open the dashboard.
Open `http://127.0.0.1:8765` yourself when ready. Use Ctrl-C when done or pass
`--exit-after-all` if an existing UI is already up. Extraneous capture families
remain available through `opensesame demo run <target>` for ad hoc testing.

## Development

```bash
uv sync
uv run pytest
```

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
