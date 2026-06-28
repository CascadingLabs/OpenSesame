#!/usr/bin/env python3
"""CAS-217 Cloudflare Managed Challenge traversal smoke test.

Run from the OpenSesame repo with local VoidCrawl installed/available:

    cd /home/andrew/Desktop/cl/OpenSesame
    VOIDCRAWL_STEALTH_NO_RUNTIME=1 xvfb-run -a uv run python examples/cas217_managed_challenge.py

If using the sibling VoidCrawl checkout directly:

    cd /home/andrew/Desktop/cl/OpenSesame
    PYTHONPATH=/home/andrew/Desktop/cl/VoidCrawl \
    VOIDCRAWL_STEALTH_NO_RUNTIME=1 \
    xvfb-run -a uv run python examples/cas217_managed_challenge.py

Optional:

    VOIDCRAWL_STEALTH_NO_RUNTIME=1 xvfb-run -a uv run python examples/cas217_managed_challenge.py \
      --url https://2captcha.com/demo/cloudflare-turnstile-challenge \
      --timeout 20

Expected result:
- The page may start with title "Just a moment...".
- OpenSesame locates the challenge iframe/token container and sends trusted
  compositor mouse input through VoidCrawl.
- The script prints CLEARED when the title no longer looks like a Cloudflare interstitial.

OpenSesame role:
- This is a harness example for the "detect/route/click managed challenge" lane.
- VoidCrawl provides the browser/session primitive; OpenSesame owns the challenge
  state polling and click policy.

Notes:
- Set VOIDCRAWL_STEALTH_NO_RUNTIME=1 before Chrome launches.
- xvfb-run provides an invisible display for headful Chrome on Linux.
- Minimal-CDP mode trades off network capture/network-idle, OOPIF auto-attach,
  isolated-world evaluate_function, and cross-origin frame eval.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time

from voidcrawl import BrowserConfig, BrowserSession

DEFAULT_URL = "https://2captcha.com/demo/cloudflare-turnstile-challenge"


def is_cloudflare_interstitial(title: str) -> bool:
    normalized = title.strip().lower()
    return normalized in {"just a moment...", "attention required! | cloudflare"}


async def challenge_state(page: object) -> dict[str, object]:
    state = await page.eval_js(
        """
        (() => {
          const token = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
          const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
          let target = iframe;
          if (!target && token) {
            let node = token.parentElement;
            while (node && node !== document.body) {
              const candidate = node.getBoundingClientRect();
              if (candidate.width >= 100 && candidate.height >= 40) {
                target = node;
                break;
              }
              node = node.parentElement;
            }
          }
          const rect = target?.getBoundingClientRect();
          return {
            title: document.title || "",
            url: location.href,
            token_length: token?.value?.length || 0,
            iframe_rect: rect ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height} : null
          };
        })()
        """
    )
    return state if isinstance(state, dict) else {}


async def click_challenge(page: object, rect: dict[str, object]) -> bool:
    try:
        x = float(rect["x"]) + min(22.0, float(rect["width"]) / 2)
        y = float(rect["y"]) + (float(rect["height"]) / 2)
    except (KeyError, TypeError, ValueError):
        return False
    await page.dispatch_mouse_event("mouseMoved", x, y, button="none")
    await page.dispatch_mouse_event("mousePressed", x, y, button="left", click_count=1)
    await asyncio.sleep(0.08)
    await page.dispatch_mouse_event("mouseReleased", x, y, button="left", click_count=1)
    print(f"      clicked challenge at ({x:.0f}, {y:.0f})")
    return True


async def run(url: str, timeout: float, click_interval: float) -> int:
    if os.environ.get("VOIDCRAWL_STEALTH_NO_RUNTIME") != "1":
        print("ERROR: set VOIDCRAWL_STEALTH_NO_RUNTIME=1 before running this script")
        return 2

    async with BrowserSession(BrowserConfig(headless=False, stealth=True)) as browser:
        page = await browser.new_page("about:blank")
        await page.navigate(url)

        start = time.monotonic()
        last_click = -999.0
        click_count = 0
        while True:
            elapsed = time.monotonic() - start
            state = await challenge_state(page)
            title = str(state.get("title") or "")
            href = str(state.get("url") or "")
            token_length = int(state.get("token_length") or 0)
            print(f"{elapsed:5.1f}s title={title!r} token_len={token_length} url={href}")

            if not is_cloudflare_interstitial(title) or token_length > 0:
                print("CLEARED")
                return 0
            if elapsed >= timeout:
                print(f"STILL_BLOCKED clicks={click_count}")
                return 1

            rect = state.get("iframe_rect")
            if isinstance(rect, dict) and elapsed - last_click >= click_interval:
                if await click_challenge(page, rect):
                    click_count += 1
                    last_click = elapsed
            await asyncio.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--click-interval", type=float, default=5.0)
    args = parser.parse_args()

    raise SystemExit(asyncio.run(run(args.url, args.timeout, args.click_interval)))


if __name__ == "__main__":
    main()
