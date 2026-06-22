"""OpenSesame solver-on-tap MCP server (FastMCP, stdio).

OpenSesame never owns the browser. This server lets an agent that is **already**
driving a tab (VoidCrawl MCP / Playwright MCP / any CDP browser) hand that exact
tab to the local solver: each tool call attaches to the shared Chrome over
``ws_url``, **adopts** the tab by ``target_id``, runs the engine in place (token
injected / answer typed by default), then **detaches without closing** the
browser — so the minted solution stays in the agent's tab and the primary driver
just continues.

Get ``ws_url`` + ``target_id`` from the primary driver — e.g. the VoidCrawl MCP
``session_open`` result returns both. The browser must expose a reachable CDP
endpoint (launch with a remote-debugging port).

Tools
-----
- ``detect(ws_url, target_id)`` → ``{"kind": ...}`` — which wall is on the tab.
- ``solve(ws_url, target_id, family=None, ...)`` → result dict — solve it in place.

Run (stdio)::

    python -m OpenSesame.mcp.server
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from mcp.server.fastmcp import FastMCP

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.challenge import host_of
from OpenSesame.api.defaults import default_solver
from OpenSesame.api.result import Family, SolveResult

server = FastMCP("opensesame")

# Lenient aliases the agent may pass for `family` → canonical Family.
# (reCAPTCHA v2 + Turnstile auto-detect from the live page, so they're optional;
# the others must be named because VoidCrawl's probe can't tell them apart.)
_FAMILY_ALIASES: dict[str, Family] = {
    "recaptcha": Family.RECAPTCHA_V2,
    "recaptcha_v2": Family.RECAPTCHA_V2,
    "recaptcha_v2_invisible": Family.RECAPTCHA_V2_INVISIBLE,
    "turnstile": Family.TURNSTILE,
    "hcaptcha": Family.HCAPTCHA,
    "mtcaptcha": Family.MTCAPTCHA,
    "geetest": Family.GEETEST,
    "rotate": Family.ROTATE,
    "cap": Family.CAP,
    "altcha": Family.ALTCHA,
    "puzzle": Family.PUZZLE,
    "logic": Family.PUZZLE,
    "ocr": Family.OCR,
}

_solver = None


def _get_solver():
    """One process-wide Solver so models stay cached across calls.

    The base policy is default-deny; ``allow_sites`` is overridden per call with
    the adopted tab's *own* host — the agent chose to navigate there, so solving
    that host is in scope.
    """
    global _solver
    if _solver is None:
        # auto_timeout_s is generous: some engines (e.g. rotate's full sweep)
        # legitimately take ~20-40s, and a solver-on-tap call is interactive.
        _solver = default_solver(SolverPolicy.auto_only(auto_timeout_s=120.0))
    return _solver


async def _open(ws_url: str):
    """Attach a fresh CDP connection to the shared Chrome (caller closes it)."""
    from voidcrawl import BrowserConfig, BrowserSession

    session = BrowserSession(BrowserConfig(ws_url=ws_url))
    await session.__aenter__()
    return session


@server.tool()
async def detect(ws_url: str, target_id: str) -> dict[str, Any]:
    """Report the captcha kind on an already-open tab — does not solve.

    Adopts the tab at ``target_id`` on the Chrome reachable at ``ws_url`` and
    runs VoidCrawl's live probe. Returns ``{"kind": "turnstile" | "recaptcha" |
    "hcaptcha" | "cloudflare_challenge" | "datadome" | None}``. Use the result to
    decide which ``family`` (if any) to pass to :func:`solve`.
    """
    session = await _open(ws_url)
    try:
        page = await session.attach_page(target_id)
        return {"kind": await page.detect_captcha()}
    except Exception as exc:  # surface as a value, not an MCP error
        return {"kind": None, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        await session.__aexit__(None, None, None)


@server.tool()
async def solve(
    ws_url: str,
    target_id: str,
    family: str | None = None,
    image_selector: str | None = None,
    response_field_selector: str | None = None,
    apply: bool = True,
    models: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Solve the captcha on an already-open tab, in place.

    Attaches to the shared Chrome at ``ws_url``, adopts the tab ``target_id``
    (from the primary driver — e.g. VoidCrawl MCP ``session_open``), solves with
    local models, and by default resolves the solution into that very tab (token
    injected / answer typed). Detaches without closing the browser, so the
    primary driver continues and submits.

    Args:
        ws_url: CDP WebSocket endpoint of the shared Chrome.
        target_id: CDP target id of the tab to adopt.
        family: omit to auto-detect (reCAPTCHA v2 / Turnstile). Otherwise one of
            ``recaptcha_v2`` | ``turnstile`` | ``mtcaptcha`` | ``geetest`` |
            ``rotate`` | ``ocr``. ``ocr`` also accepts ``image_selector`` +
            ``response_field_selector``.
        apply: ``True`` (default) injects the token / types the answer into the
            tab; ``False`` returns the raw token/answer for you to relay.
        models: optional per-family model id overrides (e.g.
            ``{"recaptcha_v2_audio": "openai/whisper-base.en"}``).
        timeout: seconds before the solve is abandoned (returns a TIMEOUT value).

    Returns:
        ``{ok, status, family, token, answer, applied, confidence, solved_by,
        error, metadata, elapsed_ms}``.
    """
    session = await _open(ws_url)
    try:
        page = await session.attach_page(target_id)
        url = await page.url() or ""
        host = host_of(url)

        if family is None:
            # Prefer the rich descriptor (sitekey / widget / response-field) when
            # the browser exposes `capture_captcha`; otherwise fall back to the
            # lighter `detect_captcha` probe (kind only). Both map to a Family via
            # Challenge.from_capture; only reCAPTCHA v2 / Turnstile auto-route.
            capture_fn = getattr(page, "capture_captcha", None)
            if callable(capture_fn):
                capture = await capture_fn()
            else:
                kind = await page.detect_captcha()
                capture = {"kind": kind, "page_url": url} if kind else None
            if not capture:
                return _err(
                    "no captcha auto-detected on the tab; pass `family` explicitly "
                    "for geetest / rotate / mtcaptcha / ocr"
                )
            challenge = Challenge.from_capture(capture)
            if not challenge.host and host:
                challenge = replace(challenge, host=host)
        else:
            fam = _FAMILY_ALIASES.get(family.lower())
            if fam is None:
                return _err(
                    f"unknown family {family!r}; expected one of {sorted(_FAMILY_ALIASES)}"
                )
            if fam is Family.OCR:
                challenge = Challenge.ocr(
                    url=url,
                    image_selector=image_selector,
                    response_field_selector=response_field_selector,
                )
            else:
                challenge = Challenge(family=fam, url=url, host=host)

        overrides: dict[str, Any] = {
            "allow_sites": [challenge.host or host],
            "apply": apply,
        }
        if models:
            overrides["models"] = models

        result = await _get_solver().solve(challenge, page=page, timeout=timeout, **overrides)
        return _result_dict(result)
    except Exception as exc:  # never raise across the MCP boundary
        return _err(f"{type(exc).__name__}: {exc}")
    finally:
        await session.__aexit__(None, None, None)


def _result_dict(r: SolveResult) -> dict[str, Any]:
    return {
        "ok": r.ok,
        "status": r.status.value,
        "family": r.family.value,
        "token": r.token,
        "answer": r.answer,
        "applied": r.applied,
        "confidence": r.confidence,
        "solved_by": r.solved_by.value if r.solved_by else None,
        "error": r.error,
        "metadata": r.metadata,
        "elapsed_ms": r.timing.elapsed_ms if r.timing else None,
    }


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "status": "failed", "error": message}


def main() -> None:
    """Console-script / ``python -m`` entry point: serve over stdio."""
    server.run("stdio")


if __name__ == "__main__":
    main()
