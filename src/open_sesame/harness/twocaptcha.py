"""Helpers for 2Captcha demo targets."""

from __future__ import annotations

import re
from urllib.parse import urljoin

_EXPECTED_ANSWER_RE = re.compile(r"OK\|([A-Za-z0-9]+)")
_NORMAL_IMAGE_RE = re.compile(
    r'<img[^>]+src="([^"]+)"[^>]+alt="normal captcha example"',
    re.IGNORECASE,
)


def parse_demo_expected_answer(page_text: str) -> str | None:
    """Extract the documented demo answer from a 2Captcha page body."""

    matches = _EXPECTED_ANSWER_RE.findall(page_text)
    if not matches:
        return None
    return matches[-1]


def parse_normal_demo_image_url(page_html: str, base_url: str) -> str | None:
    """Extract the normal captcha image URL from a 2Captcha demo page."""

    match = _NORMAL_IMAGE_RE.search(page_html)
    if match is None:
        return None
    return urljoin(base_url, match.group(1))
