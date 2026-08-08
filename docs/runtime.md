# OpenSesame Runtime With VoidCrawl Browser Appliance

OpenSesame does not own the local browser container. Use VoidCrawl's browser
appliance for local human handoff, live login, CAPTCHA recovery, and same-tab
resume demos.

## 1. Start the browser appliance

From the VoidCrawl repository:

```bash
./docker/run-headful.sh -d
```

Default endpoints:

- noVNC: `http://127.0.0.1:6080`
- Native VNC: `vnc://127.0.0.1:5900`
- CDP: `http://127.0.0.1:19222/json/version`

Quick checks:

```bash
curl -fsS http://127.0.0.1:6080 >/dev/null
curl -fsS http://127.0.0.1:19222/json/version
```

## 2. Start OpenSesame

From the OpenSesame repository:

```bash
uv run opensesame serve --no-notify --no-open-on-event --no-open-prompt
```

Open the operator UI:

```bash
chromium --user-data-dir=/tmp/opensesame-operator --disable-extensions \
  http://127.0.0.1:8765/#events
```

Chromium/Chrome is the supported embedded iframe browser. If the embedded
noVNC frame cannot retain keyboard or clipboard focus, open its detached link;
it controls the same remote Chrome session.

## 3. Run a demo

In a second OpenSesame terminal:

```bash
uv run --script examples/browser_local_handoff.py
# or
uv run --script examples/live_login_handoff.py
# or
uv run opensesame demo mtcaptcha --open-ui
# or
uv run opensesame demo all
```

Solve the active browser session in the event card, then click **Mark resolved**.
VoidCrawl/OpenSesame will resume against the same CDP tab; it should not launch a
new browser after the handoff.

## CAS-253 explicit interrupt handoff

CAS-253 deliberately does **not** expose a cross-process resolver or CDP
coordinates. The process that owns the `VoidCrawl BrowserSession` must park the
page, relay a redacted event to OpenSesame, and apply the operator outcome to
that same session. Use `VoidCrawlInterruptHandoff` for this boundary; the UI
never receives a CDP WebSocket URL or target ID.

During CAS-253 development, use the feature workspace without committing its
checkout-specific path into `pyproject.toml`:

```bash
PYTHONPATH=../cas-253-voidcrawl-interrupt--VoidCrawl \
  uv run --no-sync python your_caller.py
```

```python
from opensesame.voidcrawl_interrupts import (
    VoidCrawlHandoffConfig,
    VoidCrawlInterruptHandoff,
)
from voidcrawl import InterruptRequest

handoff = VoidCrawlInterruptHandoff(
    VoidCrawlHandoffConfig(
        session_id="my-browser-session",
        novnc_url="http://127.0.0.1:6080/vnc.html",
    )
)
result = await handoff.interrupt_and_wait(
    browser,
    page,
    InterruptRequest(
        code="auth.human_required",
        summary="Human sign-in is required in the remote browser.",
    ),
    kind="authentication",
    subkind="human_credentials",
)
# `result.voidcrawl_state` is "resumed" after Resolve, or "released" after
# Fail/deny. The original caller chooses the next browser action.
```

`kind` is caller-declared: `captcha`, `manual`, `sensitive_action`, or
`authentication`. OpenSesame shows the type and uses the configured noVNC (or
other configured viewer) for the human step; it does not detect, classify, or
solve pages itself.

For a runnable UI test, start the browser appliance, then run
`uv run opensesame watch` in a second terminal. In a third terminal, run:

```bash
PYTHONPATH=../VoidCrawl \
  uv run --no-sync python examples/cas253_interrupt_handoff.py
```

It opens `https://example.com`, parks that exact tab, and displays a **manual**
card with the noVNC viewer in the HTMX UI. Click **Resolved**; the script prints
`resumed` and the same page URL. Supply `--kind captcha`,
`--kind sensitive_action`, or `--kind authentication` to exercise the other
declared types.

For a more realistic, fully local flow, run:

```bash
PYTHONPATH=../VoidCrawl \
  uv run --no-sync python examples/mock_login_interrupt_handoff.py
```

It hosts a disposable login page, opens it in the same noVNC Chrome tab, and
waits for a human to enter `operator` / `open-sesame`. After **Resolved**, it
verifies the same tab reached `/secure` with its HttpOnly mock-session cookie.

## CAS-261 local captcha + login fixture

The fixture in `opensesame/fixtures/captcha_login.py` is a fully local login
page gated by a visual challenge, so a headless caller cannot finish it alone
and the operator handoff is exercised for real:

```bash
PYTHONPATH=../VoidCrawl \
  uv run --no-sync python examples/captcha_login_handoff.py
```

Read the skewed five-character code over noVNC, sign in with
`operator` / `open-sesame`, then click **Resolved**. The script resumes the same
tab and asserts it reached `/secure` with its HttpOnly cookie.

The challenge is a *fixture*, not an anti-bot control: the answer is ordinary
DOM text and is trivially readable by anything that parses the HTML. It exists
to make a human step necessary in the demo, not to resist automation. Each
`/login` load mints a new challenge and burns the previous one, so reloading
after solving invalidates the code on screen.

This example binds OpenSesame with `bind_opensesame` when the session opens:

```python
async with (
    BrowserSession(browser_config) as browser,
    bind_opensesame(browser, handoff_config) as session,
):
    page = await session.new_page(url)
    result = await session.require_human(
        page, code="captcha.human_required", summary="...", kind="captcha"
    )
```

Declaring the handler up front makes the operator a participant in the run's
lifecycle rather than a fallback reached from an error path. The CAS-253
boundary is unchanged: this process still owns the `BrowserSession` and remains
the only thing that resumes or releases it.

## Clipboard workflow

1. Choose the credential/value in your host clipboard manager.
2. Click the target field inside the remote Chrome viewport.
3. Paste with `Ctrl`/`Cmd`+`V` or your desktop paste binding.

The appliance syncs the selected host clipboard text into the remote X clipboard
and sends a remote paste keystroke. OpenSesame stores event metadata and handoff
URLs only; it does not store credentials or clipboard contents.

## Stop or reset

From the VoidCrawl repository:

```bash
docker rm -f voidcrawl-browser
rm -rf .voidcrawl/browser
```

Rebuild only:

```bash
VOIDCRAWL_BROWSER_BUILD_ONLY=1 ./docker/run-browser.sh
```

See `../VoidCrawl/docs/browser-appliance.md` for port overrides, multi-container
runtime examples, public-bind security notes, and troubleshooting.
