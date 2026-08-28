"""Formative evidence capture — patient_reported allowed (I-S-13)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from services.pratibimb.samvaad.evidence_class import assert_known_evidence_class

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from services.pratibimb.samvaad.dhaara_projection import ProjectionSink

_STORE: list[dict[str, Any]] = []


@dataclass(frozen=True)
class FormativeEvidenceRecord:
    evidence_id: str
    evidence_class: str
    payload: dict[str, Any]
    captured_at_utc: datetime


def clear_formative_store() -> None:
    _STORE.clear()
    from services.pratibimb.samvaad.formative_projector import clear_formative_ledger

    clear_formative_ledger()


def formative_store() -> list[dict[str, Any]]:
    return list(_STORE)


def capture_formative(
    payload: dict[str, Any],
    *,
    tenant_id: str | None = None,
    session: "Session | None" = None,
    projection_sink: "ProjectionSink | None" = None,
    evidence_id: str | None = None,
    captured_at_utc: datetime | None = None,
) -> FormativeEvidenceRecord:
    evidence_class = payload["evidence_class"]
    assert_known_evidence_class(evidence_class)
    rec = FormativeEvidenceRecord(
        evidence_id=evidence_id or str(uuid4()),
        evidence_class=evidence_class,
        payload=dict(payload),
        captured_at_utc=captured_at_utc or datetime.now(timezone.utc),
    )
    _STORE.append(
        {
            "evidence_id": rec.evidence_id,
            "evidence_class": rec.evidence_class,
            "payload": rec.payload,
            "captured_at_utc": rec.captured_at_utc.isoformat(),
        }
    )
    provenance = {"source_context", "submitted_by", "session_anchor", "matcher_parameters"}
    if provenance <= set(payload):
        from services.pratibimb.samvaad.formative_projector import insert_formative_evidence

        insert_formative_evidence(
            rec,
            tenant_id=tenant_id,
            session=session,
            projection_sink=projection_sink,
        )
    return rec
