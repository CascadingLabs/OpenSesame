---
name: solve-captcha-wall
description: Use when an agent driving a browser (VoidCrawl MCP, Playwright MCP, or any CDP browser) hits a captcha or anti-bot wall — reCAPTCHA v2, Cloudflare Turnstile, GeeTest, RotateCaptcha, MTCaptcha, Cap or ALTCHA (proof-of-work), or a distorted-text/OCR captcha — and needs it solved in place on the tab it is already on, so it can keep going.
---

# Solve a captcha wall (OpenSesame solver-on-tap)

OpenSesame is a **solver on tap**: it does not own a browser. It attaches to the
Chrome you are *already* driving, adopts the *exact tab* you are on, solves the
wall in place (token injected / answer typed), and detaches — leaving the result
in your tab. You stay the primary driver; you call the solver only for the wall.

## When to use

You are driving a browser and a tab shows a captcha / "verify you are human" /
"just a moment" wall that blocks progress. Use this to solve it and continue. Do
**not** use it as a general browser — keep navigating, clicking, and submitting
with your primary browser tool.

## What you need: the tab's attach coordinates

The solver attaches over CDP, so the browser must expose a reachable
remote-debugging endpoint, and you must tell the solver *which tab*:

- **`ws_url`** — the browser's CDP WebSocket endpoint.
- **`target_id`** — the CDP target id of the exact tab.

With the **VoidCrawl MCP**: call `session_open` with a `port` (e.g. `9444`); its
result now includes `websocket_url` and `target_id`. Drive the page normally
(`session_navigate`, clicks, …) until the wall appears — the `target_id` is
stable across same-tab navigations. With **Playwright/other**: launch Chrome with
`--remote-debugging-port` and read the tab's target id from `/json/list`.

## Steps

1. **(optional) Identify the wall.** Call the OpenSesame **`detect`** tool with
   `{ws_url, target_id}`. It returns `{ "kind": "turnstile" | "recaptcha" |
   "hcaptcha" | "cloudflare_challenge" | "datadome" | null }`.

2. **Solve it.** Call the OpenSesame **`solve`** tool with `{ws_url, target_id,
   family?}`:
   - **Omit `family`** for reCAPTCHA v2 and Cloudflare Turnstile — they are
     auto-detected from the live page.
   - **Pass `family`** for the rest (the live probe can't tell them apart):
     `geetest`, `rotate`, `mtcaptcha`, `cap`, `altcha`, or `ocr`. (`cap` /
     `altcha` are proof-of-work — solved by computation, no model.) For `ocr`
     you may also pass `image_selector` and `response_field_selector`.
   - Optional: `apply` (default `true`, resolves the solution into the tab;
     set `false` to get the raw token/answer back to relay yourself), `models`
     (per-family model id overrides), `timeout` (seconds).

3. **Continue.** On `{"ok": true}` the token/answer is already in your tab
   (`applied: true` for token/typed families). Resume with your primary browser
   tool — submit the form / proceed.

4. **On failure**, read `status`, `error`, and `metadata`:
   - `metadata.route == "anti-bot"` (e.g. GeeTest behavioural reject, reCAPTCHA
     v3) means it is **not solvable here** — rotate proxy/identity and retry the
     navigation, don't re-call `solve`.
   - `status: "refused"` means the family is out of scope (reCAPTCHA v3 /
     hCaptcha) — route to your anti-bot layer.

## Notes

- The solver **adopts your existing tab** — it never opens a new tab and never
  closes the browser, so your session/cookies and the minted token stay put.
- Auto-detect only covers reCAPTCHA v2 / Turnstile; name the family otherwise.
- MCP tool names are namespaced differently per host (e.g. Claude exposes
  `mcp__opensesame__solve`); refer to the **`solve`** / **`detect`** tools of the
  `opensesame` server.
