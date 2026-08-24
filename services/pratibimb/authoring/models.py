"""SQLAlchemy models for the authoring schema."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

from services.pratibimb.authoring.constants import AUTHORING_SCHEMA

AuthoringBase = declarative_base()
_metadata = AuthoringBase.metadata


class AuthoringAlembicVersion(AuthoringBase):
    """
    Per-schema migration lineage — independent from any ledger version table.

    When Alembic is adopted for authoring, configure env.py with
    `include_schemas=True` and `version_table='alembic_version'` scoped to
    the authoring schema so rollbacks never touch ledger history.
    """

    __tablename__ = "alembic_version"
    __table_args__ = {"schema": AUTHORING_SCHEMA}

    version_num = Column(String(32), primary_key=True)


class CaseDraftRow(AuthoringBase):
    __tablename__ = "case_drafts"
    __table_args__ = (
        Index("ix_case_drafts_tenant", "tenant_id"),
        {"schema": AUTHORING_SCHEMA},
    )

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False)
    author_subject_id = Column(String, nullable=False)
    blueprint_json = Column(JSON, nullable=False)
    blueprint_version = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    current_state = Column(String, nullable=False)
    harness_version = Column(String, nullable=False)


class CaseStateTransitionRow(AuthoringBase):
    __tablename__ = "case_state_transitions"
    __table_args__ = (
        CheckConstraint(
            "(from_state = 'DRAFT' AND to_state = 'IN_REVIEW' AND dry_run_result_hash IS NOT NULL) "
            "OR NOT (from_state = 'DRAFT' AND to_state = 'IN_REVIEW')",
            name="ck_dry_run_hash_draft_to_review",
        ),
        Index("ix_case_state_transitions_draft", "draft_id"),
        {"schema": AUTHORING_SCHEMA},
    )

    id = Column(String, primary_key=True)
    draft_id = Column(
        String,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_drafts.id"),
        nullable=False,
    )
    from_state = Column(String, nullable=False)
    to_state = Column(String, nullable=False)
    actor_subject_id = Column(String, nullable=False)
    actor_scope = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    dry_run_result_hash = Column(String(64), nullable=True)
    content_hash_snapshot = Column(String(64), nullable=True)
    validation_context_hash = Column(String(64), nullable=True)
    registry_snapshot_json = Column(JSON, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class GoldenFixtureRow(AuthoringBase):
    __tablename__ = "golden_fixtures"
    __table_args__ = (
        CheckConstraint(
            "fixture_kind IN ('perfect_path', 'critical_miss')",
            name="ck_fixture_kind",
        ),
        UniqueConstraint("draft_id", "fixture_kind", name="uq_golden_fixture_kind"),
        {"schema": AUTHORING_SCHEMA},
    )

    id = Column(String, primary_key=True)
    draft_id = Column(
        String,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_drafts.id"),
        nullable=False,
    )
    fixture_kind = Column(String, nullable=False)
    trace_json = Column(JSON, nullable=False)
    expected_grade_json = Column(JSON, nullable=False)
    content_hash = Column(String(64), nullable=False)


class ApprovalEventRow(AuthoringBase):
    """Append-only approval ledger. Never UPDATE or DELETE."""

    __tablename__ = "approval_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('FORMATIVE', 'SUMMATIVE')",
            name="ck_approval_kind",
        ),
        CheckConstraint(
            "event_type IN ('GRANT', 'WITHDRAW')",
            name="ck_approval_event_type",
        ),
        CheckConstraint(
            "(kind = 'FORMATIVE' AND actor_scope = 'authoring:approve') "
            "OR (kind = 'SUMMATIVE' AND actor_scope = 'authoring:approve_summative')",
            name="ck_approval_scope_matches_kind",
        ),
        CheckConstraint(
            "(event_type = 'GRANT' AND dry_run_result_hash IS NOT NULL) "
            "OR (event_type = 'WITHDRAW')",
            name="ck_approval_grant_has_hash",
        ),
        CheckConstraint(
            "(event_type = 'WITHDRAW' AND supersedes_event_id IS NOT NULL) "
            "OR (event_type = 'GRANT')",
            name="ck_approval_withdraw_has_target",
        ),
        Index("ix_approval_events_draft", "draft_id"),
        {"schema": AUTHORING_SCHEMA},
    )

    id = Column(String, primary_key=True)
    draft_id = Column(
        String,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_drafts.id"),
        nullable=False,
    )
    event_type = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    actor_subject_id = Column(String, nullable=False)
    actor_scope = Column(String, nullable=False)
    dry_run_result_hash = Column(String(64), nullable=True)
    submit_dry_run_hash = Column(String(64), nullable=True)
    content_hash = Column(String(64), nullable=False)
    validation_context_hash = Column(String(64), nullable=True)
    reason = Column(Text, nullable=True)
    supersedes_event_id = Column(String, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class AuthoringAuditRow(AuthoringBase):
    """Authoring audit trail — separate from ledger read audit (§3.4)."""

    __tablename__ = "authoring_audit"
    __table_args__ = (
        Index("ix_authoring_audit_draft", "draft_id"),
        {"schema": AUTHORING_SCHEMA},
    )

    id = Column(String, primary_key=True)
    artifact_type = Column(String, nullable=False)
    draft_id = Column(
        String,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_drafts.id"),
        nullable=False,
    )
    case_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    from_state = Column(String, nullable=False)
    to_state = Column(String, nullable=False)
    actor_subject_id = Column(String, nullable=False)
    tenant_id = Column(String, nullable=False)
    at_utc = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    content_hash = Column(String, nullable=False)


class PublishedCaseVersionRow(AuthoringBase):
    """Append-only publish records (Phase D). Retire is a tombstone patch."""

    __tablename__ = "published_case_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "version",
            name="uq_published_case_version",
        ),
        Index("ix_published_case_versions_case_id", "case_id"),
        Index(
            "ix_published_case_versions_content_hash",
            "content_hash",
        ),
        {"schema": AUTHORING_SCHEMA},
    )

    id = Column(String, primary_key=True)
    draft_id = Column(
        String,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_drafts.id"),
        nullable=False,
    )
    tenant_id = Column(String, nullable=False)
    case_id = Column(String, nullable=False)
    version = Column(String, nullable=False)

    # Frozen at publish-time so runtime can route correctly.
    assessment_mode = Column(String, nullable=False)

    # Canonical reference used by replay/grading.
    content_hash = Column(String(64), nullable=False)

    # Build metadata, not client input.
    harness_version = Column(String, nullable=False)

    published_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    published_by = Column(String, nullable=False)

    # Retirement tombstone (denormalized for query convenience).
    retired_at = Column(DateTime(timezone=True), nullable=True)
    retired_by = Column(String, nullable=True)
    retired_reason_code = Column(String, nullable=True)
    retired_reason_text = Column(Text, nullable=True)

    # Submit freeze pinning for compile/dry-run parity.
    registry_snapshot_json = Column(JSON, nullable=True)
    validation_context_hash = Column(String(64), nullable=True)

    # Versioned golden fixture hashes for summative validation.
    fixture_hashes = Column(JSON, nullable=True)

    # Stamped at publish (or backfilled from first summative GRANT in slice 2).
    clinical_reviewer = Column(String, nullable=True)


class RetiredCaseVersionRow(AuthoringBase):
    """Append-only retirement audit (Phase D). Source of truth is transitions."""

    __tablename__ = "retired_case_versions"
    __table_args__ = (
        CheckConstraint(
            "reason_code IN ("
            "'clinical_error', 'superseded_by_new_version', 'guideline_change', "
            "'policy_change', 'operator_request'"
            ")",
            name="ck_retire_reason_code",
        ),
        Index("ix_retired_case_versions_published", "published_case_version_id"),
        Index("ix_retired_case_versions_case", "tenant_id", "case_id", "version"),
        {"schema": AUTHORING_SCHEMA},
    )

    id = Column(String, primary_key=True)
    published_case_version_id = Column(
        String,
        ForeignKey(f"{AUTHORING_SCHEMA}.published_case_versions.id"),
        nullable=False,
    )
    draft_id = Column(
        String,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_drafts.id"),
        nullable=False,
    )
    tenant_id = Column(String, nullable=False)
    case_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    retired_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    retired_by = Column(String, nullable=False)
    reason_code = Column(String, nullable=False)
    reason_text = Column(Text, nullable=True)
