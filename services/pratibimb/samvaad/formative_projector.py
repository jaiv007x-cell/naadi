"""SAMVAAD.d formative-authoritative projector for migration 022."""
from __future__ import annotations

import json
from hashlib import sha256
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.pratibimb.ledger.models import SamvaadFormativeEvidenceRow
from services.pratibimb.samvaad.dhaara_projection import (
    EvidenceProjectionSource,
    ProjectionSink,
    canonical_json,
    normalize_freshness_inputs,
    publish_source,
)
from services.pratibimb.samvaad.evidence_class import assert_known_evidence_class
from services.pratibimb.samvaad.metrics import record_evidence_insert

if TYPE_CHECKING:
    from services.pratibimb.samvaad.formative import FormativeEvidenceRecord

_LEDGER: list[dict[str, Any]] = []
_EMITTED_DIGESTS: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class FormativeLedgerRow:
    evidence_id: str
    evidence_class: str
    source_context_json: str
    submitted_by: str
    session_anchor: str
    matcher_parameters_json: str
    captured_at_utc: datetime
    tenant_id: str | None = None
    learner_pseudo_id: str | None = None
    competency_hits_json: str = "[]"
    evidence_digest: str = ""
    freshness_inputs_json: str = "{}"


def clear_formative_ledger() -> None:
    _LEDGER.clear()
    _EMITTED_DIGESTS.clear()


def formative_ledger_rows() -> list[dict[str, Any]]:
    return list(_LEDGER)


def formative_evidence_digest(
    *,
    learner_pseudo_id: str,
    evidence_class: str,
    source_context: Any,
    submitted_by: str,
    session_anchor: str,
    matcher_parameters: Any,
    competency_hits: Any,
) -> str:
    """Canonical content-plus-provenance digest; capture time is excluded."""
    payload = {
        "learner_pseudo_id": learner_pseudo_id,
        "evidence_class": evidence_class,
        "source_context": source_context,
        "submitted_by": submitted_by,
        "session_anchor": session_anchor,
        "matcher_parameters": matcher_parameters,
        "competency_hits": competency_hits,
    }
    # Match PostgreSQL 023's canonical jsonb text (sorted keys with JSON spaces).
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def insert_formative_evidence(
    record: "FormativeEvidenceRecord",
    *,
    tenant_id: str | None = None,
    session: Session | None = None,
    projection_sink: ProjectionSink | None = None,
) -> FormativeLedgerRow:
    payload = record.payload
    required = {"source_context", "submitted_by", "session_anchor", "matcher_parameters"}
    missing = required - set(payload)
    if missing:
        raise ValueError(f"missing formative provenance fields: {sorted(missing)}")
    assert_known_evidence_class(record.evidence_class)
    resolved_tenant = tenant_id or _optional_text(payload.get("tenant_id"))
    learner_pseudo_id = _optional_text(payload.get("learner_pseudo_id"))
    competency_hits = payload.get("competency_hits") or ()
    if not isinstance(competency_hits, (list, tuple)) or not all(
        isinstance(item, str) for item in competency_hits
    ):
        raise ValueError("competency_hits must be a string list")
    digest = (
        formative_evidence_digest(
            learner_pseudo_id=learner_pseudo_id,
            evidence_class=record.evidence_class,
            source_context=payload["source_context"],
            submitted_by=str(payload["submitted_by"]),
            session_anchor=str(payload["session_anchor"]),
            matcher_parameters=payload["matcher_parameters"],
            competency_hits=list(competency_hits),
        )
        if learner_pseudo_id is not None
        else ""
    )
    if session is not None and (resolved_tenant is None or learner_pseudo_id is None):
        raise ValueError(
            "SQL formative projection requires tenant_id and learner_pseudo_id"
        )
    freshness_inputs = (
        normalize_freshness_inputs(
            competency_hits,
            payload.get("freshness_inputs"),
        )
        if session is not None or projection_sink is not None
        else {}
    )
    key = (resolved_tenant or "", digest)
    if session is None and digest and key in _EMITTED_DIGESTS:
        _raise_duplicate_formative()
    row = FormativeLedgerRow(
        evidence_id=record.evidence_id,
        evidence_class=record.evidence_class,
        source_context_json=canonical_json(payload["source_context"]),
        submitted_by=str(payload["submitted_by"]),
        session_anchor=str(payload["session_anchor"]),
        matcher_parameters_json=canonical_json(payload["matcher_parameters"]),
        captured_at_utc=record.captured_at_utc,
        tenant_id=resolved_tenant,
        learner_pseudo_id=learner_pseudo_id,
        competency_hits_json=json.dumps(
            list(competency_hits), separators=(",", ":")
        ),
        evidence_digest=digest,
        freshness_inputs_json=canonical_json(freshness_inputs),
    )
    if session is not None:
        session.add(
            SamvaadFormativeEvidenceRow(
                evidence_id=row.evidence_id,
                tenant_id=row.tenant_id,
                learner_pseudo_id=row.learner_pseudo_id,
                assessment_kind="formative",
                evidence_class=row.evidence_class,
                evidence_digest=row.evidence_digest,
                source_context_json=row.source_context_json,
                submitted_by=row.submitted_by,
                session_anchor=row.session_anchor,
                matcher_parameters_json=row.matcher_parameters_json,
                competency_hits_json=row.competency_hits_json,
                freshness_inputs_json=row.freshness_inputs_json,
                captured_at_utc=row.captured_at_utc,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            record_evidence_insert(
                assessment_kind="formative",
                evidence_class=row.evidence_class,
                outcome="error",
            )
            _raise_duplicate_formative()
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
            "tenant_id": row.tenant_id,
            "learner_pseudo_id": row.learner_pseudo_id,
            "competency_hits_json": row.competency_hits_json,
            "evidence_digest": row.evidence_digest,
            "freshness_inputs_json": row.freshness_inputs_json,
        }
    )
    if digest:
        _EMITTED_DIGESTS.add(key)
    record_evidence_insert(
        assessment_kind="formative",
        evidence_class=row.evidence_class,
        outcome="success",
    )
    if projection_sink is not None:
        if row.learner_pseudo_id is None:
            raise ValueError("Dhaara formative projection requires learner_pseudo_id")
        publish_source(
            EvidenceProjectionSource(
                evidence_id=row.evidence_id,
                assessment_kind="formative",
                evidence_class=row.evidence_class,
                learner_pseudo_id=row.learner_pseudo_id,
                session_anchor=row.session_anchor,
                replay_hash=row.evidence_digest,
                competency_hits=tuple(competency_hits),
                freshness_inputs=freshness_inputs,
                captured_at_utc=row.captured_at_utc,
            ),
            projection_sink,
        )
    return row


def _optional_text(value: Any) -> str | None:
    return None if value is None or str(value) == "" else str(value)


def _raise_duplicate_formative() -> None:
    from services.pratibimb.samvaad.bias_remediation import (
        ERROR_KIND_ILLEGAL_TRANSITION,
        BiasRemediationError,
    )

    raise BiasRemediationError(
        "formative evidence already emitted for tenant/evidence_digest; "
        "refusing silent overwrite",
        error_kind=ERROR_KIND_ILLEGAL_TRANSITION,
        from_state="formative_emitted",
        to_state="formative_re_emit",
    )
