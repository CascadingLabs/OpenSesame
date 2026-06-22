"""Cap + ALTCHA proof-of-work solver tests (deterministic, no network)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from OpenSesame.api.engines._pow import (
    altcha_hash_hex,
    altcha_payload,
    altcha_solve,
    cap_derive_challenges,
    cap_prng,
    cap_solve,
)

# prng outputs captured from @cap.js/widget's real JS (node) — guards the port.
PRNG_VECTORS = [
    ("abc1", 8, "a240feb0"),
    ("abc1d", 4, "e6e4"),
    ("TOKEN_xyz1", 32, "1f80345d387883d828716cf9b8278fce"),
    ("TOKEN_xyz1d", 4, "8cf3"),
    ("hello1", 16, "7c8905e1da94749b"),
]


def test_cap_prng_matches_capjs():
    for seed, length, expected in PRNG_VECTORS:
        assert cap_prng(seed, length) == expected
        assert len(cap_prng(seed, length)) == length


def test_cap_solve_satisfies_every_target():
    token, c, s, d = "testtoken123", 5, 16, 3  # d=3 keeps it fast + deterministic
    sols = cap_solve(token, c, s, d)
    chals = cap_derive_challenges(token, c, s, d)
    assert len(sols) == c
    for (salt, target), nonce in zip(chals, sols):
        assert hashlib.sha256(f"{salt}{nonce}".encode()).hexdigest().startswith(target)


def test_altcha_solve_roundtrip():
    salt, number = "abc123salt", 4321
    challenge = altcha_hash_hex("SHA-256", f"{salt}{number}")
    assert altcha_solve(salt, challenge, maxnumber=20000) == number


def test_altcha_solve_none_when_unsolvable():
    assert altcha_solve("salt", "00" * 32, maxnumber=10) is None


def test_altcha_payload_verifies_like_a_server():
    # Build a challenge the way an ALTCHA server does, solve, then verify.
    key, salt, number = b"server-secret", "deadsalt99", 777
    challenge = altcha_hash_hex("SHA-256", f"{salt}{number}")
    signature = hmac.new(key, challenge.encode(), hashlib.sha256).hexdigest()
    chal = {"algorithm": "SHA-256", "challenge": challenge, "maxnumber": 5000,
            "salt": salt, "signature": signature}

    found = altcha_solve(salt, challenge, 5000)
    payload = altcha_payload(chal, found, took_ms=3.0)
    obj = json.loads(base64.b64decode(payload))

    # server-side verification (matches altcha verifySolution)
    assert obj["number"] == number
    assert altcha_hash_hex("SHA-256", f"{obj['salt']}{obj['number']}") == obj["challenge"]
    assert hmac.new(key, obj["challenge"].encode(), hashlib.sha256).hexdigest() == obj["signature"]
