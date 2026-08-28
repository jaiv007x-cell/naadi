"""SAMVAAD.d formative-authoritative projector for migration 022."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

from services.pratibimb.samvaad.evidence_class import assert_known_evidence_class

if TYPE_CHECKING:
    from services.pratibimb.samvaad.formative import FormativeEvidenceRecord

_LEDGER: list[dict[str, Any]] = []


@dataclass(frozen=True)
class FormativeLedgerRow:
    evidence_id: str
    evidence_class: str
    source_context_json: str
    submitted_by: str
    session_anchor: str
    matcher_parameters_json: str
    captured_at_utc: datetime


def clear_formative_ledger() -> None:
    _LEDGER.clear()


def formative_ledger_rows() -> list[dict[str, Any]]:
    return list(_LEDGER)


def insert_formative_evidence(record: "FormativeEvidenceRecord") -> FormativeLedgerRow:
    payload = record.payload
    required = {"source_context", "submitted_by", "session_anchor", "matcher_parameters"}
    missing = required - set(payload)
    if missing:
        raise ValueError(f"missing formative provenance fields: {sorted(missing)}")
    assert_known_evidence_class(record.evidence_class)
    row = FormativeLedgerRow(
        evidence_id=record.evidence_id,
        evidence_class=record.evidence_class,
        source_context_json=json.dumps(payload["source_context"], sort_keys=True),
        submitted_by=str(payload["submitted_by"]),
        session_anchor=str(payload["session_anchor"]),
        matcher_parameters_json=json.dumps(payload["matcher_parameters"], sort_keys=True),
        captured_at_utc=record.captured_at_utc,
    )
    _LEDGER.append(
        {
            "evidence_id": row.evidence_id,
            "assessment_kind": "formative",
            "evidence_class": row.evidence_class,
            "source_context_json": row.source_context_json,
            "submitted_by": row.submitted_by,
            "session_anchor": row.session_anchor,
            "matcher_parameters_json": row.matcher_parameters_json,
            "captured_at_utc": row.captured_at_utc.isoformat(),
        }
    )
    return row
