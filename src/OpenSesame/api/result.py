"""Public result + solution contracts for the OpenSesame solver API.

Failure is a *value*, not an exception: every solve attempt returns a
``SolveResult`` whose ``status`` says what happened. Only misconfiguration
(a denied site, an invalid policy) raises. This keeps the data-flywheel honest;
a timeout or a low-confidence miss is a recorded outcome, not a lost stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Family(str, Enum):
    """The captcha challenge family OpenSesame classified the descriptor as."""

    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V2_INVISIBLE = "recaptcha_v2_invisible"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    TURNSTILE = "turnstile"
    MTCAPTCHA = "mtcaptcha"
    GEETEST = "geetest"
    ROTATE = "rotate"
    CAP = "cap"
    ALTCHA = "altcha"
    PUZZLE = "puzzle"
    OCR = "ocr"
    AUDIO = "audio"
    UNKNOWN = "unknown"


class Delivery(str, Enum):
    """How the solution is consumed by the caller."""

    # A signed token to inject into the page response field (reCAPTCHA, hCaptcha…).
    TOKEN_GRANT = "token_grant"
    # A plaintext answer to type into a form field (OCR / standalone audio).
    ANSWER = "answer"


class SolvedBy(str, Enum):
    """Provenance: which kind of solver produced the result."""

    LOCAL = "local"   # a local model on this box
    HUMAN = "human"   # a person in the manual/VNC takeover path


class SolveStatus(str, Enum):
    """Terminal outcome of a solve attempt."""

    SOLVED = "solved"
    FAILED = "failed"            # ran, did not produce an accepted solution
    REFUSED = "refused"          # policy/responsible-use declined to solve
    RATE_LIMITED = "rate_limited"  # the target throttled us (surface; rotate downstream)
    TIMEOUT = "timeout"          # exceeded the configured timeout


@dataclass(frozen=True)
class TokenSolution:
    """A signed token to inject into the page (token-grant challenges)."""

    token: str

    delivery: Delivery = field(default=Delivery.TOKEN_GRANT, init=False)

    @property
    def is_token(self) -> bool:
        return True

    @property
    def is_answer(self) -> bool:
        return False

    @property
    def value(self) -> str:
        return self.token


@dataclass(frozen=True)
class AnswerSolution:
    """A plaintext answer to type into a form field (OCR / standalone audio)."""

    text: str

    delivery: Delivery = field(default=Delivery.ANSWER, init=False)

    @property
    def is_token(self) -> bool:
        return False

    @property
    def is_answer(self) -> bool:
        return True

    @property
    def value(self) -> str:
        return self.text


Solution = TokenSolution | AnswerSolution


@dataclass(frozen=True)
class Timing:
    """Wall-clock provenance for a solve attempt (epoch seconds + elapsed ms)."""

    started_at: float
    elapsed_ms: float


@dataclass(frozen=True)
class SolveResult:
    """Normalized, provenance-carrying result of one solve attempt.

    ``ok`` is true only when ``status is SOLVED``. The ``solution`` is present
    on success and ``None`` otherwise. Every other field describes *how* the
    outcome was reached, for audit + the training flywheel.
    """

    status: SolveStatus
    family: Family
    solution: Solution | None = None
    solved_by: SolvedBy | None = None
    vendor: str | None = None          # detected vendor system (e.g. "google", "hcaptcha")
    device: str | None = None
    model_id: str | None = None
    confidence: float | None = None
    host: str | None = None
    timing: Timing | None = None
    policy_id: str | None = None
    correlation_id: str | None = None
    attempts: int = 1
    # True when OpenSesame applied the solution to the live page (token injected /
    # answer typed); the default. False on the over-the-wire path where the
    # caller takes the raw token/answer and applies it themselves.
    applied: bool = False
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is SolveStatus.SOLVED

    @property
    def token(self) -> str | None:
        sol = self.solution
        return sol.token if isinstance(sol, TokenSolution) else None

    @property
    def answer(self) -> str | None:
        sol = self.solution
        return sol.text if isinstance(sol, AnswerSolution) else None

    @property
    def delivery(self) -> Delivery | None:
        return self.solution.delivery if self.solution is not None else None
