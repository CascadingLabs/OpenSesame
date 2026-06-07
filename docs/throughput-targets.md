# Throughput Targets

OpenSesame needs a gauntlet lane that measures routing and throughput before
solver quality. Some walls should be classified and handed to VoidCrawl profile
escalation rather than treated as OCR, image classification, or audio work.

## Fortress

| Field | Value |
|---|---|
| Target | Fortress |
| URL | https://fortress.theplumber.dev/ |
| Family | Cloudflare managed challenge |
| Use | Throughput and anti-bot routing benchmark |
| OpenSesame role | Detect and classify; do not OCR |
| VoidCrawl role | Browser/profile clearance and escalation |

Live inspection shows a Cloudflare managed challenge page with Turnstile assets:

- page title `Just a moment...`
- copy `Performing security verification`
- hidden `cf-turnstile-response` field
- `challenges.cloudflare.com/turnstile` script
- `/cdn-cgi/challenge-platform/` orchestration script

That makes Fortress a good target for measuring:

- requests per second for cheap HTTP classification
- challenge classification rate
- error rate under concurrency
- later, VoidCrawl profile-tier clearance rate

Run the HTTP classification throughput probe:

```bash
PYTHONPATH=src python examples/throughput_fortress.py --attempts 25 --concurrency 5
PYTHONPATH=src python examples/throughput_fortress.py --attempts 25 --concurrency 5 --json
```

Run the shallow gauntlet enumerator:

```bash
PYTHONPATH=src python examples/fortress_gauntlet.py --engine httpx --max-pages 10 --json
PYTHONPATH=/home/andrew/Desktop/cl/OpenSesame/src:/home/andrew/Desktop/cl/Yosoi uv run python /home/andrew/Desktop/cl/OpenSesame/examples/fortress_gauntlet.py --engine yosoi-auto --yosoi-path /home/andrew/Desktop/cl/Yosoi --json
```

This probe intentionally does not attempt to bypass Cloudflare. It establishes a
repeatable baseline for recognizing that the wall is an enterprise anti-bot
challenge and routing it to the browser/profile layer.

The `httpx` mode measures cheap page classification and link discovery. The
`yosoi-auto` mode calls the local Yosoi auto fetcher, which tries HTTP first and
then escalates through VoidCrawl browser tiers. Use the Yosoi mode to learn
where the OpenSesame/VoidCrawl boundary is: direct-answer CAPTCHA solving stays
in OpenSesame, while Cloudflare managed challenge clearance belongs to
VoidCrawl/Yosoi identity and profile escalation.

Current VoidCrawl headless observation: the root page remains on the Cloudflare
managed Turnstile wall, with no same-site challenge links exposed before
clearance. That is challenge 1/10 for this gauntlet until a trusted profile or
identity cascade clears the entry wall.

Current tiny live baseline:

| Probe | Attempts/pages | Concurrency | OK | Challenges | Errors | Throughput/time | Avg latency | p95 latency | Vendor |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| HTTP throughput | 3 | 2 | 3 | 3 | 0 | 12.4 rps | 108.8 ms | 116.9 ms | Cloudflare |
| HTTP gauntlet | 1 page | n/a | 1 | 1 | 0 | 152.0 ms | 127.9 ms | n/a | Cloudflare |
| Yosoi auto gauntlet | 1 page | n/a | 0 | 1 | 1 | 16731.5 ms | n/a | n/a | Cloudflare |

Current Yosoi auto result: Yosoi skipped simple HTTP after its HEAD probe,
started VoidCrawl headless, then escalated to VoidCrawl headful. Both browser
tiers remained on the Cloudflare challenge and Yosoi raised `BotDetectionError`
with Cloudflare challenge markers. That gives OpenSesame the correct routing
decision: no direct-answer CAPTCHA payload is available yet; the next work is a
VoidCrawl/Yosoi identity/profile clearance lane.
