#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "yosoi==0.0.3a21",
#   "voidcrawl==0.3.8.2",
# ]
# ///
"""Self-hosted VoidCrawl browser appliance handoff spike.

Run:

    # terminal 1, from this OpenSesame repo
    ../VoidCrawl/docker/run-browser.sh

    # terminal 2, from this OpenSesame repo
    uv run opensesame serve --no-notify --no-open-on-event --no-open-prompt

    # terminal 3, from this OpenSesame repo
    uv run --script examples/browser_local_handoff.py

Defaults match ``../VoidCrawl/docker/run-browser.sh``:
- CDP: http://127.0.0.1:19222/json/version
- remote browser handoff/live view: http://127.0.0.1:3069

The implementation delegates to ``live_login_handoff.py`` so both examples keep
one interrupt contract: generic ``handoff_url`` / ``remote_browser_url`` with
credentials typed only into the remote browser appliance.
"""

from __future__ import annotations

import asyncio

from live_login_handoff import main

if __name__ == "__main__":
    asyncio.run(main())
