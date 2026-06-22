#!/usr/bin/env python3
"""ALTCHA proof-of-work solve through the OpenSesame public API.

ALTCHA is a *computational* captcha: the client must find the number whose
``SHA-256(salt+number)`` equals the server's challenge hash. OpenSesame's
``AltchaEngine`` brute-forces it (no model, no human, no browser) and emits the
base64 solution payload the widget would. Here we generate a challenge exactly as
an ALTCHA server does, solve it, and verify the payload the same way the server's
``verifySolution`` would — re-hashing ``salt+number`` and checking the HMAC.

    .../venvs/solver/bin/python examples/solve_altcha_live.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import sys

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver
from OpenSesame.api.result import Family

HMAC_KEY = b"altcha-demo-hmac-key"


def make_challenge(maxnumber: int = 200_000) -> tuple[dict, int]:
    """Mint an ALTCHA challenge the way a server would (the hidden answer is `number`)."""
    salt = secrets.token_hex(12)
    number = secrets.randbelow(maxnumber)
    challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
    signature = hmac.new(HMAC_KEY, challenge.encode(), hashlib.sha256).hexdigest()
    return {
        "algorithm": "SHA-256", "challenge": challenge,
        "maxnumber": maxnumber, "salt": salt, "signature": signature,
    }, number


async def main() -> int:
    chal, hidden_number = make_challenge()

    solver = default_solver(SolverPolicy.auto_only(allow_sites=["altcha.demo"], apply=False))
    challenge = Challenge(
        family=Family.ALTCHA, url="https://altcha.demo/", host="altcha.demo",
        metadata={"altcha_challenge": chal},
    )
    result = await solver.solve(challenge, page=None, timeout=60)
    if not result.ok:
        print(f"✗ not solved: {result.status.value} ({result.error})")
        return 1

    obj = json.loads(base64.b64decode(result.token))
    rehash = hashlib.sha256(f"{obj['salt']}{obj['number']}".encode()).hexdigest()
    sig_ok = hmac.new(HMAC_KEY, obj["challenge"].encode(), hashlib.sha256).hexdigest() == obj["signature"]
    verified = obj["number"] == hidden_number and rehash == obj["challenge"] and sig_ok

    if verified:
        print(f"✓ PASSED — ALTCHA solved: number={obj['number']} of [0,{chal['maxnumber']}], "
              f"payload verifies server-side ({result.timing.elapsed_ms:.0f}ms, "
              f"by={result.solved_by.value})")
        return 0
    print(f"✗ payload did not verify: {obj}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
