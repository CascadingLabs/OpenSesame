"""The challenge descriptor OpenSesame consumes (it never owns the browser).

VoidCrawl detects the wall and hands over a descriptor via ``capture_captcha``
(`CaptchaInfo`: kind, sitekey, widget rect, response-field selector, existing
token, action/cdata, page url). ``Challenge`` mirrors that, plus the host and
the classified ``Family``. The live page handle is passed *separately* to
``solve()`` — it is imperative state, not part of the serializable descriptor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from open_sesame.api.result import Family


# Map VoidCrawl's CaptchaKind tag -> our Family. Sub-variants (v2 vs invisible,
# enterprise) are refined by the engine once it reads the live widget.
_KIND_TO_FAMILY: dict[str, Family] = {
    "recaptcha": Family.RECAPTCHA_V2,
    "hcaptcha": Family.HCAPTCHA,
    "turnstile": Family.TURNSTILE,
}


@dataclass(frozen=True)
class WidgetRect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Challenge:
    """A captcha challenge to solve, independent of the live page handle."""

    family: Family
    url: str
    host: str
    vendor_kind: str | None = None          # raw VoidCrawl kind tag
    sitekey: str | None = None
    widget_selector: str | None = None
    widget_rect: WidgetRect | None = None
    response_field_selector: str | None = None
    existing_token: str | None = None
    action: str | None = None               # Turnstile data-action
    cdata: str | None = None                # Turnstile data-cdata
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_capture(cls, info: Any) -> Challenge:
        """Build from a VoidCrawl ``capture_captcha`` result (object or dict)."""

        get = info.get if isinstance(info, dict) else lambda k, d=None: getattr(info, k, d)
        kind = get("kind")
        url = str(get("page_url") or get("url") or "")
        rect_raw = get("widget_rect")
        rect = None
        if rect_raw is not None:
            rget = rect_raw.get if isinstance(rect_raw, dict) else lambda k: getattr(rect_raw, k)
            rect = WidgetRect(
                x=float(rget("x")),
                y=float(rget("y")),
                width=float(rget("width")),
                height=float(rget("height")),
            )
        return cls(
            family=family_for_kind(kind),
            url=url,
            host=host_of(url),
            vendor_kind=str(kind) if kind else None,
            sitekey=get("sitekey"),
            widget_selector=get("widget_selector"),
            widget_rect=rect,
            response_field_selector=get("response_field_selector"),
            existing_token=get("existing_token"),
            action=get("action"),
            cdata=get("cdata"),
        )

    @classmethod
    def ocr(cls, url: str = "", **metadata: Any) -> Challenge:
        """A direct-answer OCR/text challenge (no token, no widget)."""

        return cls(family=Family.OCR, url=url, host=host_of(url), metadata=metadata)


def family_for_kind(kind: Any) -> Family:
    if not kind:
        return Family.UNKNOWN
    return _KIND_TO_FAMILY.get(str(kind).lower(), Family.UNKNOWN)


def host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except (ValueError, TypeError):
        return ""
