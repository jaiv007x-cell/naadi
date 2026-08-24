"""Append-only audit trail for ledger_read queries.

Invariants enforced at the schema level:
- No UPDATE, no DELETE (enforced by role grants in migration 006).
- Every row carries tenant_id, subject_pseudo_id, scope, outcome.
- Query parameters stored as SHA-256 hash + canonical JSON length, never raw.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
)

from services.pratibimb.ledger.models import Base


class LedgerReadAuditRow(Base):
    __tablename__ = "ledger_read_audit"

    query_id = Column(String, primary_key=True)
    at_utc = Column(DateTime(timezone=True), nullable=False, index=True)

    tenant_id = Column(String, nullable=False, index=True)
    subject_pseudo_id = Column(String, nullable=False, index=True)
    actor_subject_id = Column(String, nullable=True)
    caller_kind = Column(String, nullable=False)

    scope = Column(String, nullable=False)
    query_kind = Column(String, nullable=False)
    query_params_hash = Column(String, nullable=False)
    query_params_bytes = Column(Integer, nullable=False)

    outcome = Column(String, nullable=False)
    result_row_count = Column(Integer, nullable=True)
    k_anonymity_floor_applied = Column(Integer, nullable=True)
    error_kind = Column(String, nullable=True)

    request_id = Column(String, nullable=True, index=True)
    duration_ms = Column(Integer, nullable=False)
    result_fingerprint = Column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('ok','scope_denied','insufficient_cohort','error')",
            name="ck_audit_outcome",
        ),
        CheckConstraint(
            "caller_kind IN ("
            "'preceptor','analyst','service','authoring',"
            "'ncvet_audit','ncvet_issuer','ncvet_verifier','ncvet_regrader',"
            "'samvaad_verifier'"
            ")",
            name="ck_audit_caller_kind",
        ),
        CheckConstraint(
            "(outcome = 'ok' AND result_row_count IS NOT NULL) "
            "OR (outcome <> 'ok' AND result_row_count IS NULL)",
            name="ck_audit_row_count_iff_ok",
        ),
        Index("ix_audit_tenant_at", "tenant_id", "at_utc"),
        Index("ix_audit_tenant_subject_at", "tenant_id", "subject_pseudo_id", "at_utc"),
    )
