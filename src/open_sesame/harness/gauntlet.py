"""Gauntlet runner primitives for multi-page CAPTCHA/anti-bot targets."""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

import httpx

from open_sesame.harness.antibot import AntiBotVerdict, classify_antibot_response, extract_page_links

ProbeEngine = Literal["httpx", "yosoi-auto", "voidcrawl-profile"]


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
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        captcha_kind = str(self.metadata.get("captcha_kind") or "").lower()
        token_present = bool(self.metadata.get("turnstile_token_present"))
        token_length = int(self.metadata.get("turnstile_token_length") or 0)
        title = self.title.lower()
        visible_challenge = captcha_kind in {"turnstile", "cloudflare", "cloudflare_challenge"}
        pending_turnstile = token_present and token_length == 0
        cloudflare_title = title in {"just a moment...", "attention required! | cloudflare"}
        return (
            self.verdict.challenged
            or self.status_code in {403, 429, 503}
            or visible_challenge
            or pending_turnstile
            or cloudflare_title
        )

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
            "metadata": self.metadata,
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


async def crawl_gauntlet_voidcrawl_profile(
    start_url: str,
    *,
    profile_dir: str | Path,
    max_pages: int = 10,
    timeout: float = 30.0,
    headful: bool = True,
    chrome_executable: str | None = None,
    screenshot_dir: str | Path | None = None,
) -> tuple[GauntletSummary, tuple[GauntletPageResult, ...]]:
    """Crawl a Fortress-style gauntlet through one persistent VoidCrawl profile."""

    try:
        from voidcrawl import BrowserConfig, BrowserSession
    except Exception as exc:  # pragma: no cover - optional dependency path
        elapsed_ms = 0.0
        result = GauntletPageResult(
            url=start_url,
            engine="voidcrawl-profile",
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )
        return summarize_gauntlet(start_url, "voidcrawl-profile", elapsed_ms, 1, [result]), (result,)

    profile_path = Path(profile_dir).expanduser().resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    if screenshot_dir is not None:
        Path(screenshot_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)

    config_kwargs: dict[str, object] = {
        "headless": not headful,
        "stealth": True,
        "chrome_executable": chrome_executable,
        "user_data_dir": str(profile_path),
    }
    config_kwargs = {key: value for key, value in config_kwargs.items() if value is not None}

    queue: deque[str] = deque([start_url])
    seen: set[str] = {start_url}
    results: list[GauntletPageResult] = []
    started = time.perf_counter()
    try:
        config = BrowserConfig(**config_kwargs)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        result = GauntletPageResult(
            url=start_url,
            engine="voidcrawl-profile",
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            error=(
                f"{type(exc).__name__}: {exc}. Install a VoidCrawl build that exposes "
                "BrowserConfig.user_data_dir for profile-backed anti-bot sessions."
            ),
            metadata={"profile_dir": str(profile_path), "headful": headful},
        )
        return summarize_gauntlet(start_url, "voidcrawl-profile", elapsed_ms, 1, [result]), (result,)

    async with BrowserSession(config) as browser:
        page = await browser.new_page("about:blank")
        while queue and len(results) < max_pages:
            url = queue.popleft()
            screenshot_path = None
            if screenshot_dir is not None:
                screenshot_path = (
                    Path(screenshot_dir).expanduser().resolve()
                    / f"fortress-{len(results) + 1:02d}.png"
                )
            result = await probe_voidcrawl_profile_page(
                page,
                url,
                timeout=timeout,
                profile_dir=profile_path,
                headful=headful,
                screenshot_path=screenshot_path,
            )
            results.append(result)
            if result.blocked:
                continue
            for link in result.links:
                if link not in seen and len(seen) < max_pages:
                    seen.add(link)
                    queue.append(link)

    elapsed_ms = (time.perf_counter() - started) * 1000
    return summarize_gauntlet(start_url, "voidcrawl-profile", elapsed_ms, len(seen), results), tuple(results)


async def probe_voidcrawl_profile_page(
    page: object,
    url: str,
    *,
    timeout: float,
    profile_dir: Path,
    headful: bool,
    screenshot_path: Path | None = None,
) -> GauntletPageResult:
    """Probe one URL with an already-launched VoidCrawl page."""

    started = time.perf_counter()
    try:
        response = await page.goto(url, timeout=timeout, capture_endpoints=True)
        await asyncio.sleep(0.5)
        elapsed_ms = (time.perf_counter() - started) * 1000
        html = response.html or await page.content()
        final_url = str(getattr(response, "url", None) or await page.url() or url)
        title = await page.title() or extract_title(html)
        captcha_kind = await _maybe_call(page, "detect_captcha")
        turnstile = await extract_turnstile_state(page, html)
        if screenshot_path is not None:
            png = await page.screenshot_png()
            screenshot_path.write_bytes(png)

        raw_status_code = getattr(response, "status_code", None)
        status_code = int(raw_status_code) if raw_status_code is not None else None
        headers = dict(getattr(response, "headers", {}) or {})
        metadata = {
            "captcha_kind": captcha_kind,
            "profile_dir": str(profile_dir),
            "headful": headful,
            "screenshot_path": str(screenshot_path) if screenshot_path is not None else None,
            **turnstile,
        }
        return GauntletPageResult(
            url=final_url,
            engine="voidcrawl-profile",
            ok=True,
            status_code=status_code,
            elapsed_ms=elapsed_ms,
            title=title,
            html_length=len(html),
            links=extract_page_links(html, final_url),
            verdict=classify_antibot_response(html, status_code=status_code, headers=headers),
            metadata={key: value for key, value in metadata.items() if value is not None},
        )
    except Exception as exc:  # pragma: no cover - live browser path
        elapsed_ms = (time.perf_counter() - started) * 1000
        return GauntletPageResult(
            url=url,
            engine="voidcrawl-profile",
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
            metadata={"profile_dir": str(profile_dir), "headful": headful},
        )


async def extract_turnstile_state(page: object, html: str) -> dict[str, object]:
    """Extract Turnstile sitekey/action/token evidence from DOM and markup."""

    state = await _maybe_eval_js(
        page,
        """
        (() => {
          const root = document.querySelector('[data-sitekey], .cf-turnstile, iframe[src*="challenges.cloudflare.com"]');
          const token = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
          const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
          return {
            sitekey: root?.getAttribute('data-sitekey') || null,
            action: root?.getAttribute('data-action') || null,
            cdata: root?.getAttribute('data-cdata') || null,
            iframe_src: iframe?.getAttribute('src') || null,
            token_present: !!token,
            token_length: token?.value?.length || 0
          };
        })()
        """,
    )
    if not isinstance(state, dict):
        state = {}

    iframe_src = str(state.get("iframe_src") or "")
    sitekey = state.get("sitekey") or extract_turnstile_sitekey(html, iframe_src=iframe_src)
    return {
        "turnstile_sitekey": sitekey,
        "turnstile_action": state.get("action"),
        "turnstile_cdata": state.get("cdata"),
        "turnstile_iframe_src": iframe_src or None,
        "turnstile_token_present": bool(state.get("token_present")),
        "turnstile_token_length": int(state.get("token_length") or 0),
    }


def extract_turnstile_sitekey(html: str, *, iframe_src: str = "") -> str | None:
    """Find a Turnstile sitekey in markup or Cloudflare challenge iframe URLs."""

    for pattern in (
        r"""data-sitekey=["']([^"']+)["']""",
        r"""sitekey["']?\s*[:=]\s*["']([^"']+)["']""",
    ):
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)

    iframe_urls = re.findall(r"""<iframe[^>]+src=["']([^"']+)["']""", html, re.IGNORECASE)
    for raw_url in (iframe_src, *iframe_urls):
        query = parse_qs(urlparse(raw_url.replace("&amp;", "&")).query)
        for key in ("sitekey", "k"):
            value = query.get(key)
            if value:
                return value[0]
    return None


async def _maybe_call(obj: object, method: str) -> object | None:
    try:
        return await getattr(obj, method)()
    except Exception:
        return None


async def _maybe_eval_js(page: object, expression: str) -> object | None:
    try:
        return await page.eval_js(expression)
    except Exception:
        return None


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
