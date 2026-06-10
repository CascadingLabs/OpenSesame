"""VoidCrawl-backed Google Search probe for reCAPTCHA/anti-bot telemetry."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlparse


GOOGLE_SEARCH_URL = "https://www.google.com/search"


@dataclass(frozen=True)
class GoogleSearchProbeResult:
    query: str
    url: str
    ok: bool
    elapsed_ms: float
    title: str = ""
    final_url: str = ""
    captcha_kind: str | None = None
    blocked: bool = False
    signals: tuple[str, ...] = field(default_factory=tuple)
    result_count: int = 0
    screenshot_path: str | None = None
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "url": self.url,
            "ok": self.ok,
            "elapsed_ms": self.elapsed_ms,
            "title": self.title,
            "final_url": self.final_url,
            "captcha_kind": self.captcha_kind,
            "blocked": self.blocked,
            "signals": list(self.signals),
            "result_count": self.result_count,
            "screenshot_path": self.screenshot_path,
            "error": self.error,
        }


def build_google_search_url(query: str, *, hl: str = "en", gl: str = "us") -> str:
    params = urlencode({"q": query, "hl": hl, "gl": gl, "pws": "0"})
    return f"{GOOGLE_SEARCH_URL}?{params}"


def classify_google_search_page(
    *,
    html: str,
    text: str,
    title: str,
    final_url: str,
    captcha_kind: str | None = None,
    result_count: int = 0,
) -> tuple[bool, tuple[str, ...]]:
    haystack = f"{html}\n{text}\n{title}\n{final_url}".lower()
    parsed = urlparse(final_url)
    signals: list[str] = []

    if parsed.path.startswith("/sorry/"):
        signals.append("google-sorry-url")
    if "our systems have detected unusual traffic" in haystack:
        signals.append("google-unusual-traffic-copy")
    if "google.com/recaptcha/" in haystack or "www.google.com/recaptcha/" in haystack:
        signals.append("google-recaptcha-script")
    if "g-recaptcha" in haystack:
        signals.append("g-recaptcha-widget")
    if "please show you're not a robot" in haystack or "not a robot" in haystack:
        signals.append("not-a-robot-copy")
    if captcha_kind:
        signals.append(f"voidcrawl-captcha-{captcha_kind}")
    if "before you continue to google search" in haystack:
        signals.append("google-consent-wall")

    blocked = bool(signals and result_count == 0) or any(
        signal
        in {
            "google-sorry-url",
            "google-unusual-traffic-copy",
            "google-recaptcha-script",
            "g-recaptcha-widget",
            "not-a-robot-copy",
        }
        for signal in signals
    )
    return blocked, tuple(signals)


async def run_google_search_probe(
    queries: list[str] | tuple[str, ...],
    *,
    headless: bool = True,
    chrome_executable: str | None = None,
    timeout: float = 30.0,
    pause: float = 1.5,
    screenshot_dir: str | Path | None = None,
    hl: str = "en",
    gl: str = "us",
) -> tuple[GoogleSearchProbeResult, ...]:
    try:
        from voidcrawl import BrowserConfig, BrowserSession
    except Exception as exc:  # pragma: no cover - optional dependency path
        return tuple(
            GoogleSearchProbeResult(
                query=query,
                url=build_google_search_url(query, hl=hl, gl=gl),
                ok=False,
                elapsed_ms=0.0,
                error=f"{type(exc).__name__}: {exc}",
            )
            for query in queries
        )

    screenshot_path: Path | None = None
    if screenshot_dir is not None:
        screenshot_path = Path(screenshot_dir).expanduser().resolve()
        screenshot_path.mkdir(parents=True, exist_ok=True)

    config_kwargs: dict[str, object] = {
        "headless": headless,
        "stealth": True,
        "chrome_executable": chrome_executable,
        "extra_args": ["--window-size=1365,900"],
    }
    config_kwargs = {key: value for key, value in config_kwargs.items() if value is not None}

    results: list[GoogleSearchProbeResult] = []
    async with BrowserSession(BrowserConfig(**config_kwargs)) as browser:
        page = await browser.new_page("about:blank")
        for index, query in enumerate(queries, start=1):
            result = await probe_google_search_query(
                page,
                query,
                index=index,
                timeout=timeout,
                pause=pause,
                screenshot_dir=screenshot_path,
                hl=hl,
                gl=gl,
            )
            results.append(result)
    return tuple(results)


async def probe_google_search_query(
    page: object,
    query: str,
    *,
    index: int,
    timeout: float,
    pause: float,
    screenshot_dir: Path | None,
    hl: str,
    gl: str,
) -> GoogleSearchProbeResult:
    started = time.perf_counter()
    url = build_google_search_url(query, hl=hl, gl=gl)
    try:
        await page.goto(url, timeout=timeout, capture_endpoints=True)
        if pause > 0:
            await asyncio.sleep(pause)

        html = str(await page.content())
        title = str(await page.title() or "")
        final_url = str(await page.url() or url)
        text = str(await page.eval_js("document.body?.innerText || ''") or "")
        captcha_kind = await _detect_captcha(page)
        result_count = int(
            await page.eval_js(
                "document.querySelectorAll('a[href] h3, div.g, div[data-sokoban-container]').length"
            )
            or 0
        )
        shot = None
        if screenshot_dir is not None:
            shot_path = screenshot_dir / f"google-search-{index:02d}.png"
            shot_path.write_bytes(await page.screenshot_png())
            shot = str(shot_path)

        blocked, signals = classify_google_search_page(
            html=html,
            text=text,
            title=title,
            final_url=final_url,
            captcha_kind=captcha_kind,
            result_count=result_count,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return GoogleSearchProbeResult(
            query=query,
            url=url,
            ok=True,
            elapsed_ms=elapsed_ms,
            title=title,
            final_url=final_url,
            captcha_kind=captcha_kind,
            blocked=blocked,
            signals=signals,
            result_count=result_count,
            screenshot_path=shot,
        )
    except Exception as exc:  # pragma: no cover - live browser path
        elapsed_ms = (time.perf_counter() - started) * 1000
        return GoogleSearchProbeResult(
            query=query,
            url=url,
            ok=False,
            elapsed_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _detect_captcha(page: object) -> str | None:
    detector = getattr(page, "detect_captcha", None)
    if detector is None:
        return None
    try:
        detected = detector()
        if hasattr(detected, "__await__"):
            detected = await detected
    except Exception:
        return None
    return str(detected) if detected else None
