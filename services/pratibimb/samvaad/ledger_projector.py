"""SAMVAAD.c — summative evidence ledger projector (migration 021)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.pratibimb.ledger.models import SamvaadSummativeEvidenceRow
from services.pratibimb.samvaad.dhaara_projection import (
    EvidenceProjectionSource,
    ProjectionSink,
    publish_source,
)
from services.pratibimb.samvaad.metrics import record_evidence_insert
from services.pratibimb.samvaad.summative import SummativeEvidenceRecord

_LEDGER: list[dict[str, Any]] = []
_EMITTED_DIGESTS: set[tuple[str, str]] = set()


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
    learner_pseudo_id: str | None = None
    session_anchor: str | None = None


def clear_summative_ledger() -> None:
    _LEDGER.clear()
    _EMITTED_DIGESTS.clear()


def summative_ledger_rows() -> list[dict[str, Any]]:
    return list(_LEDGER)


def summative_eligible_rows() -> list[dict[str, Any]]:
    """Grade reads use an explicit allowlist, even if storage is contaminated."""
    from services.pratibimb.samvaad.evidence_class import SUMMATIVE_ELIGIBLE_CLASSES

    return [
        row
        for row in _LEDGER
        if row.get("assessment_kind", "summative") == "summative"
        and row.get("evidence_class") in SUMMATIVE_ELIGIBLE_CLASSES
    ]


def insert_summative_evidence(
    *,
    tenant_id: str,
    record: SummativeEvidenceRecord,
    session: Session | None = None,
    projection_sink: ProjectionSink | None = None,
    evidence_id: str | None = None,
    captured_at_utc: datetime | None = None,
) -> SummativeLedgerRow:
    import json

    key = (tenant_id, record.transcript_digest)
    if session is None and key in _EMITTED_DIGESTS:
        _raise_duplicate_summative()
    learner_pseudo_id = _optional_text(record.extras.get("learner_pseudo_id"))
    session_anchor = _optional_text(record.extras.get("session_anchor"))
    if session is not None and (learner_pseudo_id is None or session_anchor is None):
        raise ValueError(
            "SQL summative projection requires learner_pseudo_id and session_anchor"
        )
    captured = captured_at_utc or datetime.now(timezone.utc)
    row = SummativeLedgerRow(
        evidence_id=evidence_id or str(uuid4()),
        tenant_id=tenant_id,
        transcript_digest=record.transcript_digest,
        grader_version=record.grader_version,
        rubric_schema_version=record.rubric_schema_version,
        artifact_schema_version=record.artifact_schema_version,
        evidence_class=record.evidence_class,
        framework_citation_anchor=record.framework_citation_anchor or None,
        competency_hits_json=json.dumps(
            list(record.competency_hits), separators=(",", ":")
        ),
        captured_at_utc=captured,
        learner_pseudo_id=learner_pseudo_id,
        session_anchor=session_anchor,
    )
    if session is not None:
        session.add(
            SamvaadSummativeEvidenceRow(
                evidence_id=row.evidence_id,
                tenant_id=row.tenant_id,
                learner_pseudo_id=row.learner_pseudo_id,
                session_anchor=row.session_anchor,
                transcript_digest=row.transcript_digest,
                grader_version=row.grader_version,
                rubric_schema_version=row.rubric_schema_version,
                artifact_schema_version=row.artifact_schema_version,
                assessment_kind="summative",
                evidence_class=row.evidence_class,
                framework_citation_anchor=row.framework_citation_anchor,
                competency_hits_json=row.competency_hits_json,
                captured_at_utc=row.captured_at_utc,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            record_evidence_insert(
                assessment_kind="summative",
                evidence_class=row.evidence_class,
                outcome="error",
            )
            _raise_duplicate_summative()
    _LEDGER.append(
        {
            "evidence_id": row.evidence_id,
            "assessment_kind": "summative",
            "tenant_id": row.tenant_id,
            "transcript_digest": row.transcript_digest,
            "grader_version": row.grader_version,
            "rubric_schema_version": row.rubric_schema_version,
            "artifact_schema_version": row.artifact_schema_version,
            "evidence_class": row.evidence_class,
            "framework_citation_anchor": row.framework_citation_anchor,
            "competency_hits_json": row.competency_hits_json,
            "captured_at_utc": row.captured_at_utc.isoformat(),
            "learner_pseudo_id": row.learner_pseudo_id,
            "session_anchor": row.session_anchor,
        }
    )
    _EMITTED_DIGESTS.add(key)
    record_evidence_insert(
        assessment_kind="summative",
        evidence_class=row.evidence_class,
        outcome="success",
    )
    if projection_sink is not None:
        if row.learner_pseudo_id is None or row.session_anchor is None:
            raise ValueError(
                "Dhaara summative projection requires learner_pseudo_id and session_anchor"
            )
        publish_source(
            EvidenceProjectionSource(
                evidence_id=row.evidence_id,
                assessment_kind="summative",
                evidence_class=row.evidence_class,
                learner_pseudo_id=row.learner_pseudo_id,
                session_anchor=row.session_anchor,
                replay_hash=row.transcript_digest,
                competency_hits=record.competency_hits,
                captured_at_utc=row.captured_at_utc,
            ),
            projection_sink,
        )
    return row


def _optional_text(value: Any) -> str | None:
    return None if value is None or str(value) == "" else str(value)


def _raise_duplicate_summative() -> None:
    from services.pratibimb.samvaad.bias_remediation import (
        ERROR_KIND_ILLEGAL_TRANSITION,
        BiasRemediationError,
    )

    raise BiasRemediationError(
        "summative evidence already emitted for tenant/transcript_digest; "
        "refusing silent overwrite",
        error_kind=ERROR_KIND_ILLEGAL_TRANSITION,
        from_state="summative_emitted",
        to_state="summative_re_emit",
    )
