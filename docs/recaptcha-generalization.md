# reCAPTCHA generalization & scope

How far the v1 reCAPTCHA engines generalize, where they stop, and the path past
that line. Short version: **the architecture is vendor-agnostic; the v1 engine
*implementations* are same-origin only.**

## What generalizes (the architecture)

The seams are vendor-neutral and already cover the common variants:

- **`Family` → `Engine` routing** (`solver.py`) — adding a vendor/variant is
  registering an engine, not touching the core.
- **Strategy composite** (`engines/recaptcha.py`) — reCAPTCHA v2 is solved by an
  ordered list of strategies (audio-first, image-grid fallback). Audio is the
  preferred path: a reliable local token mint. Pin one with
  `policy.models["recaptcha_v2_strategy"] = "audio" | "grid"`.
- **`Challenge` descriptor** (`challenge.py`) — built from VoidCrawl's live DOM
  probe (`detect_captcha` / `capture_captcha`), carrying sitekey + on-screen
  `widget_rect`. The descriptor already has what a coordinate engine needs.
- **Provider registry** (`registry.py`, `builtin.py`) — models are load-once,
  process-cached, named in policy. Swapping the ViT / Whisper / OCR model is a
  config change.
- **`inject_captcha_token`** — token application is identical across reCAPTCHA v2,
  Enterprise, and (future) hCaptcha; the response field is the only difference.

### v2 + Enterprise

reCAPTCHA v2 and reCAPTCHA **Enterprise** serve byte-identical challenge DOM;
only the iframe `src` path differs (`recaptcha/api2/` vs `recaptcha/enterprise/`).
The engines match **both** (`engines/_recaptcha_dom.py`), so Enterprise is covered
on the same-origin path with no per-site code. Enterprise sitekeys flow through
`Challenge.from_capture` unchanged.

### Out-of-scope families route, they don't "fail"

reCAPTCHA **v3** is score-based — there is no challenge to crack, only a
reputation/behavior signal — and **hCaptcha** / **Turnstile** are out of v1
scope. These are a *detect-and-route* concern, not a solve target: the solver
returns `SolveStatus.REFUSED` with `metadata={"route": "anti-bot"}` and a clear
reason, so the caller hands off to the anti-bot/proxy layer instead of retrying a
phantom solve. (Routing detail tracked in CAS-192.)

## Where v1 stops (the cross-origin wall)

The v1 engines read the challenge through the bframe's `contentDocument`:

```js
const f = document.querySelector('iframe[src*="api2/bframe"], iframe[src*="enterprise/bframe"]');
```

That works only when the challenge iframe is **same-origin** — Google's own
`recaptcha/api2/demo`, or any same-origin embed. On a real third-party site the
bframe/anchor iframes are served from `google.com`, so `contentDocument` is
`null` and every DOM read/click/type fails.

v1 makes this **honest and machine-detectable** rather than a vague error. The
`_GRID_STATE` / `_AUDIO_STATE` probes split the failure:

| DOM probe result | Meaning | `SolveResult` |
|---|---|---|
| no `iframe` matches | no reCAPTCHA on the page | `FAILED`, "no reCAPTCHA challenge frame on page" |
| `iframe` present, `contentDocument` null | **cross-origin** (real site) | `FAILED`, `metadata={"cross_origin": True}`, "needs the coordinate engine (V2)" |

A caller (or the harness) can branch on `metadata["cross_origin"]` to know the
challenge is real but out of reach for the same-origin engine.

## The path past the wall (V2 — CAS-212)

**Policy: DOM-first when same-origin, coordinates when forced cross-origin** — the
engine auto-detects via the `cross_origin` signal above.

- **Vision — feasible with today's VoidCrawl.** Screenshot the `widget_rect`
  region from `capture_captcha`, classify tiles with the existing local ViT, and
  click at **coordinates** (`dispatch_mouse_event` / `click_visual_coords`)
  instead of DOM `td.click()`. No new browser primitive required.
- **Audio — blocked on a VoidCrawl primitive.** The side-door reads the signed
  MP3 URL from the bframe DOM, which is unreadable cross-origin. It needs **one
  of**: (a) frame-scoped JS eval (run a snippet inside the cross-origin reCAPTCHA
  frame), or (b) network response-body capture (read the audio URL off the
  network layer). Until one lands, audio stays same-origin-only — and audio
  remains the preferred path wherever it *is* reachable.

Tracked in **CAS-212** (cross-origin coordinate engine + the two VoidCrawl
prerequisites).

## TL;DR

| | Same-origin (`api2/demo`, embeds) | Cross-origin (real third-party) |
|---|---|---|
| **v2 / Enterprise grid** | ✅ v1 (DOM clicks) | ⏭️ V2 coordinate engine (CAS-212) |
| **v2 audio side-door** | ✅ v1 (preferred) | ⏭️ needs VoidCrawl frame-eval / response-body (CAS-212) |
| **v3 / hCaptcha / Turnstile** | ↩️ REFUSED → anti-bot route (CAS-192) | ↩️ same |
| **OCR / distorted-text** | ✅ v1 (no iframe; same-origin by nature) | n/a |
