"""Gauntlet runner primitives for multi-page CAPTCHA/anti-bot targets."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from open_sesame.harness.antibot import AntiBotVerdict, classify_antibot_response, extract_page_links

ProbeEngine = Literal["httpx", "yosoi-auto"]


@dataclass(frozen=True)
class GauntletPageResult:
    url: str
    engine: ProbeEngine
    ok: bool
    status_code: int | None
    elapsed_ms: float
    title: str = ""
    html_length: int = 0
    links: tuple[str, ...] = field(default_factory=tuple)
    verdict: AntiBotVerdict = field(
        default_factory=lambda: AntiBotVerdict(vendor=None, challenge_type=None, confidence=0.0)
    )
    error: str = ""

    @property
    def blocked(self) -> bool:
        return self.verdict.challenged or self.status_code in {403, 429, 503}

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "engine": self.engine,
            "ok": self.ok,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "title": self.title,
            "html_length": self.html_length,
            "links": list(self.links),
            "verdict": self.verdict.as_dict(),
            "blocked": self.blocked,
            "error": self.error,
        }


@dataclass(frozen=True)
class GauntletSummary:
    start_url: str
    engine: ProbeEngine
    visited: int
    blocked: int
    errors: int
    discovered: int
    elapsed_ms: float

    def as_dict(self) -> dict[str, object]:
        return {
            "start_url": self.start_url,
            "engine": self.engine,
            "visited": self.visited,
            "blocked": self.blocked,
            "errors": self.errors,
            "discovered": self.discovered,
            "elapsed_ms": self.elapsed_ms,
        }


async def crawl_gauntlet_httpx(
    start_url: str,
    *,
    max_pages: int = 10,
    timeout: float = 15.0,
) -> tuple[GauntletSummary, tuple[GauntletPageResult, ...]]:
    queue: deque[str] = deque([start_url])
    seen: set[str] = {start_url}
    results: list[GauntletPageResult] = []
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"user-agent": "OpenSesame gauntlet"}) as client:
        while queue and len(results) < max_pages:
            url = queue.popleft()
            result = await probe_httpx_page(client, url, timeout=timeout)
            results.append(result)
            if result.blocked:
                continue
            for link in result.links:
                if link not in seen and len(seen) < max_pages:
                    seen.add(link)
                    queue.append(link)

    elapsed_ms = (time.perf_counter() - started) * 1000
    return summarize_gauntlet(start_url, "httpx", elapsed_ms, len(seen), results), tuple(results)


async def probe_httpx_page(client: httpx.AsyncClient, url: str, *, timeout: float) -> GauntletPageResult:
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(client.get(url), timeout=timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000
        html = response.text
        return GauntletPageResult(
            url=str(response.url),
            engine="httpx",
            ok=True,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            title=extract_title(html),
            html_length=len(html),
            links=extract_page_links(html, str(response.url)),
            verdict=classify_antibot_response(html, status_code=response.status_code, headers=response.headers),
        )
    except Exception as exc:  # pragma: no cover - live network path
        elapsed_ms = (time.perf_counter() - started) * 1000
        return GauntletPageResult(
            url=url,
            engine="httpx",
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )


async def probe_yosoi_auto(
    url: str,
    *,
    yosoi_path: str | Path,
    timeout: int = 30,
) -> GauntletPageResult:
    """Probe one URL through local Yosoi's auto fetcher when available."""

    import sys

    started = time.perf_counter()
    path = str(Path(yosoi_path).resolve())
    if path not in sys.path:
        sys.path.insert(0, path)

    try:
        from yosoi.core.fetcher import create_fetcher
        from yosoi.utils.exceptions import BotDetectionError
    except Exception as exc:  # pragma: no cover - optional dependency path
        elapsed_ms = (time.perf_counter() - started) * 1000
        return GauntletPageResult(
            url=url,
            engine="yosoi-auto",
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            error=(
                f"{type(exc).__name__}: {exc}. Run from the Yosoi environment, "
                "for example: PYTHONPATH=/path/to/OpenSesame/src:/path/to/Yosoi "
                "uv run python /path/to/OpenSesame/examples/fortress_gauntlet.py "
                "--engine yosoi-auto --yosoi-path /path/to/Yosoi"
            ),
        )

    try:
        async with create_fetcher("auto", timeout=timeout, force=True) as fetcher:
            result = await fetcher.fetch(url)
        elapsed_ms = (time.perf_counter() - started) * 1000
        html = result.html or ""
        return GauntletPageResult(
            url=result.url,
            engine="yosoi-auto",
            ok=result.success,
            status_code=result.status_code,
            elapsed_ms=elapsed_ms,
            title=extract_title(html),
            html_length=len(html),
            links=extract_page_links(html, result.url),
            verdict=classify_antibot_response(html, status_code=result.status_code, headers=result.headers or {}),
            error=result.block_reason or "",
        )
    except BotDetectionError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return GauntletPageResult(
            url=url,
            engine="yosoi-auto",
            ok=False,
            status_code=exc.status_code,
            elapsed_ms=elapsed_ms,
            verdict=AntiBotVerdict(
                vendor="cloudflare" if any("cloudflare" in i.lower() or "cf-" in i.lower() for i in exc.indicators) else None,
                challenge_type=exc.captcha_kind or "bot_detection",
                confidence=0.8,
                signals=tuple(exc.indicators),
            ),
            error=str(exc),
        )
    except Exception as exc:  # pragma: no cover - optional live dependency path
        elapsed_ms = (time.perf_counter() - started) * 1000
        return GauntletPageResult(
            url=url,
            engine="yosoi-auto",
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )


def summarize_gauntlet(
    start_url: str,
    engine: ProbeEngine,
    elapsed_ms: float,
    discovered: int,
    results: list[GauntletPageResult],
) -> GauntletSummary:
    return GauntletSummary(
        start_url=start_url,
        engine=engine,
        visited=len(results),
        blocked=sum(1 for result in results if result.blocked),
        errors=sum(1 for result in results if not result.ok),
        discovered=discovered,
        elapsed_ms=elapsed_ms,
    )


def extract_title(html: str) -> str:
    import re

    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return " ".join(match.group(1).split())
