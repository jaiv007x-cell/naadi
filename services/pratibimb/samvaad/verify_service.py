"""SAMVAAD.c — verify orchestration (emit-always, flag-gated SoT write)."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from services.pratibimb.samvaad.config import SAMVAAD_LIVE_WRITE_ALLOW, SAMVAAD_VERIFIER_LIVE_EMIT
from services.pratibimb.samvaad.evidence_class import assert_summative_evidence_class, summative_eligible
from services.pratibimb.samvaad.formative import capture_formative
from services.pratibimb.samvaad.ledger_projector import insert_summative_evidence
from services.pratibimb.samvaad.metrics import (
    REASON_PATIENT_REPORTED,
    summative_rejected_total,
)
from services.pratibimb.samvaad.summative import capture_summative
from services.pratibimb.samvaad.verifier_emit import (
    B_STUB_EMIT_PARAM_KEYS,
    emit_samvaad_verifier_audit,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from services.pratibimb.samvaad.dhaara_projection import ProjectionSink


def _summative_would_mutate(*, dry_run: bool, evidence_class: str) -> bool:
    if dry_run:
        return False
    if not SAMVAAD_LIVE_WRITE_ALLOW:
        return False
    try:
        return summative_eligible(evidence_class)
    except ValueError:
        return False


def verify_samvaad(
    body: dict[str, Any],
    *,
    tenant_id: str = "test-tenant",
    sink: Any | None = None,
    fail_closed: bool | None = None,
    ledger_session: "Session | None" = None,
    projection_sink: "ProjectionSink | None" = None,
    learner_pseudo_id: str | None = None,
    session_anchor: str | None = None,
) -> dict[str, Any]:
    """
    Shape A + pin 2: always emit; flag gates live_emit stamp and SoT INSERT.
    """
    assessment_kind = str(body.get("assessment_kind", "summative"))
    dry_run = True if "dry_run" not in body else bool(body["dry_run"])
    payload = dict(body.get("payload") or body.get("evidence") or {})
    if learner_pseudo_id is not None:
        payload.setdefault("learner_pseudo_id", learner_pseudo_id)
    if session_anchor is not None:
        payload.setdefault("session_anchor", session_anchor)
    evidence_class = str(payload.get("evidence_class", ""))

    would_mutate = _summative_would_mutate(dry_run=dry_run, evidence_class=evidence_class)
    live_emit = SAMVAAD_VERIFIER_LIVE_EMIT
    if fail_closed is None:
        fail_closed = live_emit

    emit_params: dict[str, Any] = {
        "path": "/v1/samvaad/verify",
        "live_emit": live_emit,
        "dry_run": dry_run,
        "would_mutate": would_mutate,
        "assessment_kind": assessment_kind,
    }
    if payload.get("transcript_digest"):
        emit_params["transcript_digest_fingerprint"] = payload["transcript_digest"][:16]

    emit_samvaad_verifier_audit(
        outcome="ok",
        params=emit_params,
        sink=sink,
        fail_closed=fail_closed,
    )

    if assessment_kind == "formative":
        rec = capture_formative(
            payload,
            tenant_id=tenant_id,
            session=ledger_session,
            projection_sink=projection_sink,
        )
        return {
            "status": "ok",
            "assessment_kind": "formative",
            "evidence_id": rec.evidence_id,
            "dry_run": dry_run,
        }

    if evidence_class:
        assert_summative_evidence_class(evidence_class)

    if dry_run:
        return {"status": "ok", "dry_run": True, "inserted": False}

    if not SAMVAAD_LIVE_WRITE_ALLOW:
        return {"status": "ok", "dry_run": False, "inserted": False, "live_write": False}

    record = capture_summative(payload)
    row = insert_summative_evidence(
        tenant_id=tenant_id,
        record=record,
        session=ledger_session,
        projection_sink=projection_sink,
    )
    return {
        "status": "ok",
        "dry_run": False,
        "inserted": True,
        "evidence_id": row.evidence_id,
    }


def emit_param_keys_for_matrix() -> frozenset[str]:
    """Matrix #14 — superset of .b stub param keys."""
    return B_STUB_EMIT_PARAM_KEYS | frozenset(
        {"live_emit", "dry_run", "would_mutate", "assessment_kind", "path"}
    )


def patient_reported_rejection_counter() -> int:
    return summative_rejected_total(reason=REASON_PATIENT_REPORTED)
