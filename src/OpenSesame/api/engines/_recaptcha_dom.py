"""Shared reCAPTCHA DOM selectors + cross-origin handling.

reCAPTCHA v2 and reCAPTCHA **Enterprise** serve byte-identical challenge DOM;
only the iframe ``src`` path differs (``recaptcha/api2/`` vs
``recaptcha/enterprise/``). Match both so the same-origin engines cover
enterprise too. Defined once here and substituted into each engine's JS via
:func:`with_selectors`.

These engines read the challenge through the bframe's ``contentDocument``, which
only works when the frame is **same-origin** (Google's own ``api2/demo``). On a
real third-party site the bframe is cross-origin and ``contentDocument`` is
null; :func:`unreadable_result` turns that into an honest, machine-detectable
``FAILED(cross_origin=True)`` rather than a vague "DOM unreadable".
"""

from __future__ import annotations

from typing import Any

from OpenSesame.api.challenge import Challenge
from OpenSesame.api.result import SolveResult, SolveStatus

# bframe = the challenge iframe; anchor = the checkbox iframe. Both v2 + enterprise.
BFRAME_SELECTOR = 'iframe[src*="api2/bframe"], iframe[src*="enterprise/bframe"]'
ANCHOR_SELECTOR = 'iframe[src*="api2/anchor"], iframe[src*="enterprise/anchor"]'


def with_selectors(js: str) -> str:
    """Substitute the shared selector placeholders into a JS snippet."""

    return js.replace("__BFRAME_SEL__", BFRAME_SELECTOR).replace("__ANCHOR_SEL__", ANCHOR_SELECTOR)


def unreadable_result(challenge: Challenge, state: Any, *, strategy: str) -> SolveResult:
    """Map an unreadable ``__STATE__`` (no ``ok``) to an honest FAILED result.

    Splits the two reasons a same-origin read fails: ``cross-origin`` (frame
    present, ``contentDocument`` null — the real-site case, needs the V2
    coordinate engine) vs ``no-frame`` (no reCAPTCHA on the page at all).
    """

    reason = state.get("reason") if isinstance(state, dict) else None
    if reason == "cross-origin":
        return SolveResult(
            status=SolveStatus.FAILED, family=challenge.family,
            error="cross-origin challenge frame — needs the coordinate engine (V2)",
            metadata={"strategy": strategy, "cross_origin": True},
        )
    return SolveResult(
        status=SolveStatus.FAILED, family=challenge.family,
        error="no reCAPTCHA challenge frame on page",
        metadata={"strategy": strategy},
    )
