"""Passive provenance audit log.

Every terminal solve outcome appends one JSONL record: host, family, method
(local|human), status, model, device, policy, timing, correlation id. This is
both the responsible-use defensibility artifact (a person solving in VNC is
materially different from forging tokens at scale, and the log makes that
legible) and the substrate for the offline fine-tuning flywheel. It is a passive
subscriber on the outcome; never a branch in the solve hot path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OpenSesame.api.result import SolveResult


class AuditLog:
    """Append-only JSONL writer. ``path=None`` disables (no-op)."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None

    def record(self, result: SolveResult, *, now: float) -> None:
        if self.path is None:
            return
        rec: dict[str, Any] = {
            "ts": now,
            "host": result.host,
            "family": result.family.value,
            "method": result.solved_by.value if result.solved_by else None,
            "status": result.status.value,
            "delivery": result.delivery.value if result.delivery else None,
            "vendor": result.vendor,
            "model_id": result.model_id,
            "device": result.device,
            "confidence": result.confidence,
            "policy_id": result.policy_id,
            "correlation_id": result.correlation_id,
            "attempts": result.attempts,
            "elapsed_ms": result.timing.elapsed_ms if result.timing else None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec) + "\n")
