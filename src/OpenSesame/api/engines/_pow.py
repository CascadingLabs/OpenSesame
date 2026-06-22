"""Proof-of-work solvers for the Cap and ALTCHA captcha families.

Both are *computational* captchas, not perceptual ones: the answer is a number
(or numbers) whose hash meets a target. No model, no human — OpenSesame just does
the work the browser would, faster, in Python.

- **Cap** (@cap.js): ``POST {ep}challenge`` returns ``{challenge:{c,s,d}, token}``;
  the ``c`` subchallenges are *derived* from ``token`` with a fnv1a+xorshift PRNG
  (replicated below, byte-for-byte with the widget), each solved by finding the
  smallest ``nonce`` with ``sha256(salt+nonce)`` hex starting with ``target``;
  ``POST {ep}redeem {token, solutions:[nonce…]}`` mints the ``cap-token``.
- **ALTCHA**: a challenge ``{algorithm, challenge, salt, maxnumber, signature}``;
  find ``number`` in ``[0, maxnumber]`` with ``sha256(salt+number)`` hex equal to
  ``challenge``; the solution payload is base64 JSON placed in the ``altcha`` field.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

# ── Cap PRNG (must match @cap.js/widget exactly) ────────────────────────────


def _to_int32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - 0x100000000 if (n & 0x80000000) else n


def _js_shl(n: int, b: int) -> int:
    return _to_int32((_to_int32(n) << (b & 31)) & 0xFFFFFFFF)


def _fnv1a(seed: str) -> int:
    h = 2166136261
    for ch in seed:
        h = _to_int32(h) ^ ord(ch)
        h = h + _js_shl(h, 1) + _js_shl(h, 4) + _js_shl(h, 7) + _js_shl(h, 8) + _js_shl(h, 24)
    return h & 0xFFFFFFFF


def cap_prng(seed: str, length: int) -> str:
    """Deterministic hex string of ``length`` chars — xorshift32 seeded by fnv1a(seed)."""
    state = _fnv1a(seed)
    out: list[str] = []
    have = 0
    while have < length:
        state = (state ^ ((state << 13) & 0xFFFFFFFF)) & 0xFFFFFFFF
        state = (state ^ (state >> 17)) & 0xFFFFFFFF
        state = (state ^ ((state << 5) & 0xFFFFFFFF)) & 0xFFFFFFFF
        out.append(f"{state:08x}")
        have += 8
    return "".join(out)[:length]


def cap_derive_challenges(token: str, c: int, s: int, d: int) -> list[tuple[str, str]]:
    """The ``c`` ``(salt, target)`` subchallenges derived from ``token`` (i = 1..c)."""
    return [(cap_prng(f"{token}{i}", s), cap_prng(f"{token}{i}d", d)) for i in range(1, c + 1)]


def cap_solve_one(salt: str, target: str) -> int:
    nonce = 0
    while not hashlib.sha256(f"{salt}{nonce}".encode()).hexdigest().startswith(target):
        nonce += 1
    return nonce


def cap_solve(token: str, c: int, s: int, d: int) -> list[int]:
    """Solve every Cap subchallenge; returns the nonces in challenge order."""
    return [cap_solve_one(salt, target) for salt, target in cap_derive_challenges(token, c, s, d)]


# ── ALTCHA ──────────────────────────────────────────────────────────────────

_ALGOS = {"SHA-256": "sha256", "SHA-384": "sha384", "SHA-512": "sha512"}


def altcha_hash_hex(algorithm: str, data: str) -> str:
    return hashlib.new(_ALGOS.get(algorithm, "sha256"), data.encode()).hexdigest()


def altcha_solve(salt: str, challenge: str, maxnumber: int, algorithm: str = "SHA-256") -> int | None:
    """Smallest ``number`` in ``[0, maxnumber]`` with ``hash(salt+number) == challenge``."""
    for number in range(int(maxnumber) + 1):
        if altcha_hash_hex(algorithm, f"{salt}{number}") == challenge:
            return number
    return None


def altcha_payload(challenge_obj: dict[str, Any], number: int, took_ms: float = 0.0) -> str:
    """The base64 JSON the ``altcha`` form field carries (matches the widget)."""
    payload = {
        "algorithm": challenge_obj.get("algorithm", "SHA-256"),
        "challenge": challenge_obj["challenge"],
        "number": number,
        "salt": challenge_obj["salt"],
        "signature": challenge_obj.get("signature", ""),
        "took": int(took_ms),
    }
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
