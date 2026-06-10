# reCAPTCHA generalization & scope

How far the reCAPTCHA engines generalize and where they stop. Short version: the
architecture is vendor-agnostic, and as of **VoidCrawl 0.3.5** the engines drive
the challenge **cross-origin** — so they work on real third-party sites, not just
Google's same-origin `api2/demo`.

## What generalizes (the architecture)

The seams are vendor-neutral and cover the common variants:

- **`Family` → `Engine` routing** (`solver.py`) — adding a vendor/variant is
  registering an engine, not touching the core.
- **Strategy composite** (`engines/recaptcha.py`) — reCAPTCHA v2 is solved by an
  ordered list of strategies (audio-first, image-grid fallback). Audio is the
  preferred path: a reliable local token mint. Pin one with
  `policy.models["recaptcha_v2_strategy"] = "audio" | "grid"`.
- **`Challenge` descriptor** (`challenge.py`) — built from VoidCrawl's live DOM
  probe (`detect_captcha` / `capture_captcha`), carrying sitekey + on-screen
  `widget_rect`.
- **Provider registry** (`registry.py`, `builtin.py`) — models are load-once,
  process-cached, named in policy.
- **`inject_captcha_token`** — token application is identical across reCAPTCHA v2,
  Enterprise, and (future) hCaptcha; only the response field differs.

### v2 + Enterprise

reCAPTCHA v2 and reCAPTCHA **Enterprise** serve byte-identical challenge DOM; only
the frame URL path differs (`recaptcha/api2/` vs `recaptcha/enterprise/`). The
engines match **both** frame patterns (`engines/_recaptcha_dom.py`,
`BFRAME_PATTERNS` / `ANCHOR_PATTERNS`), so Enterprise is covered with no per-site
code.

### Out-of-scope families route, they don't "fail"

reCAPTCHA **v3** is score-based — there is no challenge to crack — and **hCaptcha**
/ **Turnstile** are out of scope. These are detect-and-route, not solve targets:
the solver returns `SolveStatus.REFUSED` with `metadata={"route": "anti-bot"}` so
the caller hands off to the anti-bot/proxy layer. (Routing detail: CAS-192.)

## Cross-origin: how it works (VoidCrawl 0.3.5)

A reCAPTCHA challenge lives in two iframes — the `anchor` checkbox frame and the
`bframe` challenge frame. On `api2/demo` they are same-origin; on a **real
third-party site** they are served from `google.com`, so the page's own
JavaScript sees `iframe.contentDocument === null` (same-origin policy).

That restriction binds *page script*, not the controlling debugger. VoidCrawl
0.3.5's `page.eval_js_in_frame(url_pattern, expr)` runs `expr` inside the target
frame's **own** CDP execution context — where `document` is the frame's document
and the origin check is satisfied. The engines drive every challenge through
`FrameAccess` (`engines/_recaptcha_dom.py`), which calls `eval_js_in_frame`, so
the **same code path** handles same-origin and cross-origin frames:

- **Grid** — read the tile structure from inside the bframe; the parent supplies
  the iframe element's on-page rect (readable cross-origin — only
  `contentDocument` is blocked), so the screenshot is cropped in page
  coordinates; tiles are clicked via the frame's DOM (`td.click()`).
- **Audio** — read the signed MP3 URL from inside the bframe (the read that was
  impossible cross-origin before 0.3.5), transcribe locally, set + verify, harvest
  the token from the parent.

Verified live on `2captcha.com/demo/recaptcha-v2` (page on `2captcha.com`,
reCAPTCHA frames on `google.com`): the parent's `contentDocument` read returns
`NULL (cross-origin)` while `eval_js_in_frame` reads the checkbox state from
inside the google.com anchor frame.

### Launch prerequisite (cross-origin only) — owned by the browser-launcher

Chrome field-trial-isolates a few origins (notably `google.com`) into a separate
renderer process regardless of the usual flags, which would put the reCAPTCHA
frames out of `eval_js_in_frame`'s reach. The session must be launched with:

```python
BrowserConfig(extra_args=["disable-site-isolation-trials"])
```

**This is the browser-launcher's job, not OpenSesame's, and not a `SolverPolicy`
field.** OpenSesame consumes a VoidCrawl page and never launches the browser
(project invariant), so it cannot — and does not — set this flag. In the
Cascading stack the browser-owner is **Yosoi's fetcher** (`yosoi/core/fetcher/`);
that is where the flag belongs. It is an isolation-weakening opt-in, so VoidCrawl
does not set it by default either.

### Failure contract — clear, typed, actionable

When OpenSesame cannot drive the challenge frame it does **not** fail vaguely. It
returns a `FAILED` `SolveResult` whose `metadata["reason"]` is a stable code the
caller can branch on:

| `metadata.reason` | Cause | Fix (and who owns it) |
|---|---|---|
| `frame_isolated` | frame is cross-origin + out-of-process | browser-launcher adds `disable-site-isolation-trials` (also `metadata.frame_isolated=True`, `metadata.remediation=...`) |
| `voidcrawl_too_old` | page has no `eval_js_in_frame` | upgrade `voidcrawl>=0.3.5` |
| `frame_absent` | no reCAPTCHA frame on the page | nothing to solve |

The `error` string spells out the same thing in prose (e.g. *"cannot drive the
reCAPTCHA challenge frame: it is cross-origin and isolated out-of-process. The
browser/session owner (e.g. Yosoi's fetcher) must launch with
extra_args=[\"disable-site-isolation-trials\"]"*). Internally these are the
typed `FrameUnreachable(reason=...)` exception; the engine converts it to the
value above so a batch crawl keeps per-item isolation instead of crashing.
(Same-origin `api2/demo` never hits any of these.)

## What's still hard (not a frame-access problem)

- **Audio is reputation-gated.** Google rate-limits the audio side-door per IP
  (a `doscaptcha` "try again later" block); the engine surfaces this as
  `RATE_LIMITED` so downstream rotates proxy/profile. A fresh datacenter IP that
  has run several solves will hit it — on `api2/demo` and real sites alike. This
  is an IP-reputation wall, independent of cross-origin.
- **Visual-grid token mint is risk-gated.** Strong classification still only
  mints when the selected set exactly matches reCAPTCHA's expectation
  (boundary tiles) and its risk engine is satisfied. Audio is the more reliable
  path where the IP isn't throttled.

## TL;DR

| | Same-origin (`api2/demo`) | Cross-origin (real third-party) |
|---|---|---|
| **v2 / Enterprise grid** | ✅ frame-eval DOM clicks | ✅ frame-eval DOM clicks (+ `disable-site-isolation-trials`) |
| **v2 audio side-door** | ✅ (preferred; IP-reputation gated) | ✅ frame-eval reads the MP3 URL (+ flag; IP-reputation gated) |
| **v3 / hCaptcha / Turnstile** | ↩️ REFUSED → anti-bot route (CAS-192) | ↩️ same |
| **OCR / distorted-text** | ✅ (no iframe) | n/a |

History: the cross-origin engine + the VoidCrawl frame-eval prerequisite were
tracked as CAS-212; 0.3.5 delivered the prerequisite and this PR wires the engines
onto it.
