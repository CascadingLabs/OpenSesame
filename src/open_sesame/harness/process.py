"""Small process-output parsing helpers for example harnesses."""

from __future__ import annotations


def parse_key_value_output(output: str, key: str) -> str | None:
    prefix = f"{key}="
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    return None


def parse_float_key_value_output(output: str, key: str) -> float:
    value = parse_key_value_output(output, key)
    if value is None:
        return 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except ValueError:
        return 0.0
