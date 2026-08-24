"""F.a: project ``session_ledger`` rows into redacted NCVET projection."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.pratibimb.ledger.models import (
    PublishedCaseCorpusRow,
    SessionEvidenceProjectionRow,
    SessionLedgerRow,
)
from services.pratibimb.ledger_read.ncvet_redaction import redact_case_context_json


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _empty_case_context() -> str:
    return "{}"


class EvidenceProjector:
    """Write path only — builds ``runtime.session_evidence_projection`` from ledger."""

    def __init__(self, ledger_session: Session, corpus_session: Session) -> None:
        self._ledger = ledger_session
        self._corpus = corpus_session

    def project_one(
        self,
        session_id: str,
        *,
        tenant_id: str,
    ) -> SessionEvidenceProjectionRow | None:
        row = self._ledger.get(SessionLedgerRow, session_id)
        if row is None:
            return None
        case_context = _empty_case_context()
        corpus = self._corpus.scalars(
            select(PublishedCaseCorpusRow).where(
                PublishedCaseCorpusRow.tenant_id == tenant_id,
                PublishedCaseCorpusRow.case_id == row.case_id,
                PublishedCaseCorpusRow.version == row.case_version,
            )
        ).first()
        if corpus is not None:
            case_context = redact_case_context_json(corpus.envelope_json)

        projected = self._corpus.get(SessionEvidenceProjectionRow, session_id)
        if projected is None:
            projected = SessionEvidenceProjectionRow(session_id=session_id)
            self._corpus.add(projected)

        projected.tenant_id = tenant_id
        projected.learner_pseudo_id = row.learner_pseudo_id
        projected.cohort_id = row.cohort_id
        projected.case_id = row.case_id
        projected.case_version = row.case_version
        projected.finalized_at_utc = row.finalized_at_utc
        projected.physio_engine_version = row.physio_engine_version
        projected.rubric_version = row.rubric_version
        projected.replay_hash = row.replay_hash
        projected.blueprint_content_hash = row.blueprint_content_hash
        projected.blueprint_source = row.blueprint_source
        projected.grade_total = row.grade_total
        projected.grade_passed = row.grade_passed
        projected.axis_normalized = row.axis_normalized
        projected.evidence = row.evidence
        projected.flags = row.flags
        projected.actions = row.actions
        projected.case_context_json = case_context
        projected.projected_at = _utc_now()
        self._corpus.flush()
        return projected
