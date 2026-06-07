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

This probe intentionally does not attempt to bypass Cloudflare. It establishes a
repeatable baseline for recognizing that the wall is an enterprise anti-bot
challenge and routing it to the browser/profile layer.

Current tiny live baseline:

| Attempts | Concurrency | OK | Challenges | Errors | Throughput | Avg latency | p95 latency | Vendor |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 3 | 2 | 3 | 3 | 0 | 12.4 rps | 108.8 ms | 116.9 ms | Cloudflare |
