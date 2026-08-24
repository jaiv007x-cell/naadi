"""SAMVAAD.c observability — I-S-13 labeled counter."""
from __future__ import annotations

# Labeled counter (not distinct counter per reason) — H.c surface enum discipline
SUMMATIVE_REJECTED_COUNTER = "samvaad_summative_rejected_total"
REASON_PATIENT_REPORTED = "patient_reported"

_rejected: dict[str, int] = {"patient_reported": 0}


def record_summative_rejected(*, reason: str) -> None:
    if reason not in _rejected:
        _rejected[reason] = 0
    _rejected[reason] += 1


def summative_rejected_total(*, reason: str) -> int:
    return _rejected.get(reason, 0)


def reset_summative_metrics() -> None:
    _rejected.clear()
