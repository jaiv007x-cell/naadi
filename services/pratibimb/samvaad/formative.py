"""Formative evidence capture — patient_reported allowed (I-S-13)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from services.pratibimb.samvaad.evidence_class import assert_known_evidence_class

_STORE: list[dict[str, Any]] = []


@dataclass(frozen=True)
class FormativeEvidenceRecord:
    evidence_id: str
    evidence_class: str
    payload: dict[str, Any]
    captured_at_utc: datetime


def clear_formative_store() -> None:
    _STORE.clear()


def formative_store() -> list[dict[str, Any]]:
    return list(_STORE)


def capture_formative(payload: dict[str, Any]) -> FormativeEvidenceRecord:
    evidence_class = payload["evidence_class"]
    assert_known_evidence_class(evidence_class)
    rec = FormativeEvidenceRecord(
        evidence_id=str(uuid4()),
        evidence_class=evidence_class,
        payload=dict(payload),
        captured_at_utc=datetime.now(timezone.utc),
    )
    _STORE.append(
        {
            "evidence_id": rec.evidence_id,
            "evidence_class": rec.evidence_class,
            "payload": rec.payload,
            "captured_at_utc": rec.captured_at_utc.isoformat(),
        }
    )
    return rec
