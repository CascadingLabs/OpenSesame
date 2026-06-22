#!/usr/bin/env python3
"""Cap (@cap.js) proof-of-work solve through the OpenSesame Cap engine.

Cap is a *computational* captcha: the server returns ``{c, s, d}`` + a ``token``;
the ``c`` subchallenges are derived from the token with a fnv1a+xorshift PRNG
(replicated byte-for-byte from the widget), and each is solved by finding a
``nonce`` whose ``sha256(salt+nonce)`` hex starts with ``target``. OpenSesame does
this in Python and redeems the nonces for a ``cap-token``.

Points at a live Cap endpoint (default: the fortress instance). Cap's optional
*instrumentation* anti-bot layer (browser-side, beyond the proof-of-work) is
reported when present — the PoW is still computed and shown.

    .../venvs/solver/bin/python examples/solve_cap_live.py [api-endpoint]
"""

from __future__ import annotations

import asyncio
import sys
from urllib.parse import urlparse

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver
from OpenSesame.api.result import Family

ENDPOINT = sys.argv[1] if len(sys.argv) > 1 else "https://cap.acampi.dev/41ca65db88/"


async def main() -> int:
    host = urlparse(ENDPOINT).hostname or ""
    solver = default_solver(SolverPolicy.auto_only(allow_sites=[host], apply=False))
    challenge = Challenge(
        family=Family.CAP, url=ENDPOINT, host=host,
        metadata={"cap_api_endpoint": ENDPOINT},
    )
    result = await solver.solve(challenge, page=None, timeout=120)
    md = result.metadata

    if result.ok:
        print(f"✓ PASSED — minted cap-token ({len(result.token)} chars) by solving "
              f"{md.get('subchallenges')} PoW subchallenges (difficulty {md.get('difficulty')}) "
              f"in {md.get('pow_solve_ms')}ms")
        return 0

    print(f"PoW computed: {md.get('subchallenges')} subchallenges, difficulty "
          f"{md.get('difficulty')}, {md.get('pow_solve_ms')}ms — no token: {result.error}")
    if md.get("route") == "instrumentation":
        print("  → this endpoint enables Cap's browser-side instrumentation layer "
              "(an extra anti-bot check on top of the proof-of-work).")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
