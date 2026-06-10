#!/usr/bin/env python3
"""Warm a persistent Chrome profile so reCAPTCHA sees a session with history.

Token minting on reCAPTCHA v2 is gated by a per-session risk score (IP +
fingerprint + cookie history), not by tile correctness. Without a residential
proxy, the cheapest lever is a *warm* profile: a persistent ``user_data_dir``
that already holds Google consent cookies and some benign browsing history, so
the session no longer looks freshly minted. This script visits a few ordinary
pages, settles consent, and idles like a human before closing — leaving the
profile on disk for the actor to reuse via ``--user-data-dir``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any


async def warm(args: argparse.Namespace) -> dict[str, Any]:
    from voidcrawl import BrowserConfig, BrowserSession

    visited: list[dict[str, Any]] = []
    if args.ws_url:
        # Container mode: warm the cookie jar of the Chrome already running in
        # the headful container. The actor attaching to the same ws_url shares
        # that jar, so consent/history carry into the solve.
        config_kwargs = {"ws_url": args.ws_url}
    else:
        config_kwargs = {
            "headless": not args.headful,
            "stealth": True,
            "chrome_executable": args.chrome_executable,
            "user_data_dir": str(Path(args.user_data_dir).expanduser()),
            "extra_args": ["--window-size=1365,900"],
        }
    config_kwargs = {key: value for key, value in config_kwargs.items() if value is not None}

    async with BrowserSession(BrowserConfig(**config_kwargs)) as browser:
        page = await browser.new_page("about:blank")
        for url in args.urls:
            try:
                response = await page.goto(url, timeout=args.timeout)
                await _settle_consent(page)
                # Human-ish dwell: scroll a little, pause, scroll back.
                await page.eval_js("window.scrollTo(0, Math.min(600, document.body.scrollHeight))")
                await asyncio.sleep(args.dwell)
                await page.eval_js("window.scrollTo(0, 0)")
                await asyncio.sleep(args.dwell / 2)
                cookies = await _count_cookies(page)
                visited.append(
                    {
                        "url": url,
                        "final_url": str(await page.url() or url),
                        "status_code": getattr(response, "status_code", None),
                        "cookie_count": cookies,
                    }
                )
            except Exception as exc:  # pragma: no cover - live browser path
                visited.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "user_data_dir": str(Path(args.user_data_dir).expanduser().resolve()),
        "visited": visited,
    }


async def _settle_consent(page: object) -> None:
    """Best-effort click of common same-frame consent buttons.

    Uses a single non-blocking eval_js rather than click_by_role: the latter
    BLOCKS until the element appears, so on a page without a consent button it
    hangs forever, and wrapping it in a timeout either cancels or orphans the
    CDP call — both leave the VoidCrawl page unusable for the next navigation.
    """

    script = """
(() => {
  const wanted = ['accept all','i agree','accept','alle akzeptieren','tout accepter'];
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], div[tabindex]'));
  for (const node of nodes) {
    const text = (node.innerText || node.textContent || '').trim().toLowerCase();
    if (wanted.some((w) => text === w || text.includes(w))) { node.click(); return text; }
  }
  return '';
})()
"""
    try:
        clicked = await page.eval_js(script)
        if clicked:
            await asyncio.sleep(0.8)
    except Exception:
        return


async def _count_cookies(page: object) -> int:
    try:
        value = await page.eval_js("document.cookie ? document.cookie.split('; ').length : 0")
        return int(value or 0)
    except Exception:
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--user-data-dir",
        default=".local/recaptcha-warm-profile",
        help="Persistent Chrome profile directory to create/warm.",
    )
    parser.add_argument(
        "--urls",
        nargs="*",
        default=[
            "https://www.google.com/",
            "https://www.google.com/search?q=weather",
            "https://news.google.com/",
            "https://www.wikipedia.org/",
        ],
        help="Benign pages to visit while warming the profile.",
    )
    parser.add_argument("--headful", action="store_true")
    parser.add_argument(
        "--ws-url",
        help="Attach to an existing Chrome over CDP (e.g. the headful container) "
        "and warm its cookie jar instead of launching a profile.",
    )
    parser.add_argument("--chrome-executable")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dwell", type=float, default=4.0, help="Seconds to idle per page.")
    args = parser.parse_args()

    payload = asyncio.run(warm(args))
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
