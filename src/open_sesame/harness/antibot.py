"""Lightweight anti-bot challenge classification helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class AntiBotVerdict:
    vendor: str | None
    challenge_type: str | None
    confidence: float
    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def challenged(self) -> bool:
        return self.vendor is not None and self.challenge_type is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "vendor": self.vendor,
            "challenge_type": self.challenge_type,
            "confidence": self.confidence,
            "signals": list(self.signals),
            "challenged": self.challenged,
        }


def classify_antibot_response(
    html: str,
    *,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> AntiBotVerdict:
    """Classify a fetched response as a known anti-bot challenge when obvious."""

    haystack = html.lower()
    normalized_headers = {key.lower(): value for key, value in (headers or {}).items()}
    signals: list[str] = []

    if "cf-turnstile-response" in haystack:
        signals.append("cf-turnstile-response")
    if "challenges.cloudflare.com/turnstile" in haystack:
        signals.append("cloudflare-turnstile-script")
    if "/cdn-cgi/challenge-platform/" in haystack:
        signals.append("cloudflare-challenge-platform")
    if "performing security verification" in haystack:
        signals.append("security-verification-copy")
    if "just a moment" in haystack:
        signals.append("cloudflare-title")
    if "cf-ray" in normalized_headers:
        signals.append("cf-ray-header")
    if status_code in {403, 429, 503}:
        signals.append(f"challenge-status-{status_code}")

    cloudflare_score = sum(
        signal.startswith("cloudflare")
        or signal.startswith("cf-")
        or signal == "security-verification-copy"
        for signal in signals
    )
    if cloudflare_score:
        challenge_type = "turnstile_managed" if "cf-turnstile-response" in signals else "managed"
        confidence = min(0.35 + (0.15 * len(signals)), 0.99)
        return AntiBotVerdict(
            vendor="cloudflare",
            challenge_type=challenge_type,
            confidence=confidence,
            signals=tuple(signals),
        )

    return AntiBotVerdict(
        vendor=None,
        challenge_type=None,
        confidence=0.0,
        signals=tuple(signals),
    )


def extract_page_links(html: str, base_url: str) -> tuple[str, ...]:
    """Extract same-origin links from HTML for shallow gauntlet enumeration."""

    from html.parser import HTMLParser
    from urllib.parse import urldefrag, urljoin, urlparse

    class LinkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.hrefs: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() != "a":
                return
            for key, value in attrs:
                if key.lower() == "href" and value:
                    self.hrefs.append(value)

    parser = LinkParser()
    parser.feed(html)
    base = urlparse(base_url)
    links: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        absolute, _fragment = urldefrag(urljoin(base_url, href))
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc != base.netloc:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return tuple(links)
