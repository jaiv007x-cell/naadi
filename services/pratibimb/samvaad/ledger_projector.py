"""SAMVAAD.c — summative evidence ledger projector (migration 021)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from services.pratibimb.samvaad.summative import SummativeEvidenceRecord

_LEDGER: list[dict[str, Any]] = []
_EMITTED_DIGESTS: set[str] = set()


@dataclass(frozen=True)
class SummativeLedgerRow:
    evidence_id: str
    tenant_id: str
    transcript_digest: str
    grader_version: str
    rubric_schema_version: str
    artifact_schema_version: str
    evidence_class: str
    framework_citation_anchor: str | None
    competency_hits_json: str
    captured_at_utc: datetime


def clear_summative_ledger() -> None:
    _LEDGER.clear()
    _EMITTED_DIGESTS.clear()


def summative_ledger_rows() -> list[dict[str, Any]]:
    return list(_LEDGER)


def insert_summative_evidence(
    *,
    tenant_id: str,
    record: SummativeEvidenceRecord,
) -> SummativeLedgerRow:
    if record.transcript_digest in _EMITTED_DIGESTS:
        from services.pratibimb.samvaad.bias_remediation import (
            ERROR_KIND_ILLEGAL_TRANSITION,
            BiasRemediationError,
        )

        raise BiasRemediationError(
            "summative evidence already emitted for transcript_digest; "
            "refusing silent overwrite",
            error_kind=ERROR_KIND_ILLEGAL_TRANSITION,
            from_state="summative_emitted",
            to_state="summative_re_emit",
        )
    import json

    row = SummativeLedgerRow(
        evidence_id=str(uuid4()),
        tenant_id=tenant_id,
        transcript_digest=record.transcript_digest,
        grader_version=record.grader_version,
        rubric_schema_version=record.rubric_schema_version,
        artifact_schema_version=record.artifact_schema_version,
        evidence_class=record.evidence_class,
        framework_citation_anchor=record.framework_citation_anchor or None,
        competency_hits_json=json.dumps(list(record.competency_hits)),
        captured_at_utc=datetime.now(timezone.utc),
    )
    _LEDGER.append(
        {
            "evidence_id": row.evidence_id,
            "tenant_id": row.tenant_id,
            "transcript_digest": row.transcript_digest,
            "grader_version": row.grader_version,
            "rubric_schema_version": row.rubric_schema_version,
            "artifact_schema_version": row.artifact_schema_version,
            "evidence_class": row.evidence_class,
            "framework_citation_anchor": row.framework_citation_anchor,
            "competency_hits_json": row.competency_hits_json,
            "captured_at_utc": row.captured_at_utc.isoformat(),
        }
    )
    _EMITTED_DIGESTS.add(record.transcript_digest)
    return row
