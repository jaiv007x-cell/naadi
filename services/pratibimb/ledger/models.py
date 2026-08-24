from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

from services.pratibimb.ledger.namespace import RUNTIME_SCHEMA, ensure_runtime_namespace

Base = declarative_base()

_orig_create_all = Base.metadata.create_all


def _create_all_ensuring_runtime(bind=None, **kwargs):
    """Auto-ATTACH/CREATE runtime schema so existing create_all call sites keep working."""
    if bind is not None:
        binds = bind if isinstance(bind, (list, tuple)) else [bind]
        for item in binds:
            ensure_runtime_namespace(getattr(item, "engine", item))
    return _orig_create_all(bind=bind, **kwargs)


Base.metadata.create_all = _create_all_ensuring_runtime  # type: ignore[method-assign]


class SessionLedgerRow(Base):
    """
    Atomic persisted session record for BEEMA.

    Storage shape:
      - evidence/flags/actions/axis_normalized are JSON arrays/objects.
      - values are already privacy-stripped at write time.
    """

    __tablename__ = "session_ledger"
    __table_args__ = (
        CheckConstraint(
            "blueprint_content_hash IS NULL OR length(blueprint_content_hash) = 64",
            name="ck_session_ledger_blueprint_content_hash_len",
        ),
        CheckConstraint(
            "blueprint_source IS NULL OR blueprint_source IN ('published', 'seed')",
            name="ck_session_ledger_blueprint_source_enum",
        ),
        CheckConstraint(
            "("
            "blueprint_source IS NULL OR blueprint_source <> 'published' "
            "OR (blueprint_content_hash IS NOT NULL AND length(blueprint_content_hash) = 64)"
            ") AND ("
            "blueprint_source IS NULL OR blueprint_source <> 'seed' "
            "OR blueprint_content_hash IS NULL"
            ")",
            name="ck_session_ledger_blueprint_source_hash",
        ),
    )

    session_id = Column(String, primary_key=True)
    learner_pseudo_id = Column(String, nullable=False, index=True)
    cohort_id = Column(String, nullable=False, index=True)
    case_id = Column(String, nullable=False, index=True)
    case_version = Column(String, nullable=False)

    physio_engine_version = Column(String, nullable=False, index=True)
    rubric_version = Column(String, nullable=False)
    replay_hash = Column(String, nullable=False)
    # Phase E1: nullable, no Column default — absent insert and explicit None
    # both land as SQL NULL (field absent vs None are indistinguishable).
    blueprint_content_hash = Column(String(64), nullable=True)
    # Phase E2.2.c: nullable, NO default — NULL = pre-E2.2.c legacy unknown.
    # Do not backfill. Write-once at append.
    blueprint_source = Column(String(16), nullable=True)
    finalized_at_utc = Column(DateTime(timezone=True), nullable=False, index=True)

    confirmation = Column(String, nullable=False, default="unconfirmed")
    preceptor_pseudo_id = Column(String, nullable=True)

    grade_total = Column(Float, nullable=False)
    grade_passed = Column(Boolean, nullable=False)
    axis_normalized = Column(JSON, nullable=False)
    evidence = Column(JSON, nullable=False)
    flags = Column(JSON, nullable=False)
    actions = Column(JSON, nullable=False)

    outcome_link_token = Column(String, nullable=True)


class TrustedPhysioVersion(Base):
    __tablename__ = "trusted_physio_version"

    version = Column(String, primary_key=True)
    approved_at_utc = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    approved_by = Column(String, nullable=False)
    notes = Column(String, nullable=True)


class InstitutionalUnit(Base):
    """Typed institutional slice — ward, department, rotation, or training batch."""

    __tablename__ = "institutional_unit"

    tenant_id = Column(String, primary_key=True)
    unit_id = Column(String, primary_key=True)
    unit_type = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    effective_from_utc = Column(DateTime(timezone=True), nullable=True)
    effective_to_utc = Column(DateTime(timezone=True), nullable=True)


class CohortUnitAssignmentRow(Base):
    """Time-bounded membership linking ledger cohorts to institutional units."""

    __tablename__ = "cohort_unit_assignment"

    tenant_id = Column(String, primary_key=True)
    cohort_id = Column(String, primary_key=True)
    unit_id = Column(String, primary_key=True)
    valid_from_utc = Column(DateTime(timezone=True), nullable=True)
    valid_to_utc = Column(DateTime(timezone=True), nullable=True)


class ConsentGrantRow(Base):
    """Append-only consent grant history; revocation appends — never UPDATE/DELETE."""

    __tablename__ = "consent_grant"

    grant_id = Column(String, primary_key=True)
    subject_id = Column(String, nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    scope = Column(JSON, nullable=False)
    event_type = Column(String, nullable=False)
    granted_at = Column(DateTime(timezone=True), nullable=False, index=True)
    granted_by = Column(String, nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True, index=True)
    revoked_by = Column(String, nullable=True)
    revoke_reason_code = Column(Text, nullable=True)
    supersedes_grant_id = Column(String, nullable=True, index=True)
    source = Column(String, nullable=True)


class PublishedCaseCorpusRow(Base):
    """
    Phase E2.1: runtime projection of a published (or retired) case version.

    ``id`` equals ``authoring.published_case_versions.id``.
    ``probe_only`` / ``workflow_state`` are derived from ``retired_at`` at project time.
    ``envelope_json`` is canonical JSON *text* (byte-idempotent on re-project).
    ``content_hash`` is copied from the published row — never recomputed as write source.
    """

    __tablename__ = "published_case_corpus"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "version",
            name="uq_runtime_published_case_corpus_slot",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_runtime_corpus_content_hash_len",
        ),
        CheckConstraint(
            "workflow_state IN ('PUBLISHED', 'RETIRED')",
            name="ck_runtime_corpus_workflow_state",
        ),
        CheckConstraint(
            "(probe_only = true AND workflow_state = 'RETIRED') "
            "OR (probe_only = false AND workflow_state = 'PUBLISHED')",
            name="ck_runtime_corpus_probe_matches_state",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    case_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    content_hash = Column(String(64), nullable=False)
    assessment_mode = Column(String, nullable=False)
    workflow_state = Column(String, nullable=False)
    probe_only = Column(Boolean, nullable=False)
    harness_version = Column(String, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False)
    envelope_json = Column(Text, nullable=False)
    projected_at = Column(DateTime(timezone=True), nullable=False)


class SessionEvidenceProjectionRow(Base):
    """
    F.a: redacted NCVET read projection — rebuildable from ``session_ledger``.

    ``ncvet`` read path queries this table only, never ``session_ledger`` (I-F-1).
    """

    __tablename__ = "session_evidence_projection"
    __table_args__ = ({"schema": RUNTIME_SCHEMA},)

    session_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    learner_pseudo_id = Column(String, nullable=False, index=True)
    cohort_id = Column(String, nullable=False)
    case_id = Column(String, nullable=False)
    case_version = Column(String, nullable=False)
    finalized_at_utc = Column(DateTime(timezone=True), nullable=False)
    physio_engine_version = Column(String, nullable=False)
    rubric_version = Column(String, nullable=False)
    replay_hash = Column(String, nullable=False)
    blueprint_content_hash = Column(String(64), nullable=True)
    blueprint_source = Column(String(16), nullable=True)
    grade_total = Column(Float, nullable=False)
    grade_passed = Column(Boolean, nullable=False)
    axis_normalized = Column(JSON, nullable=False)
    evidence = Column(JSON, nullable=False)
    flags = Column(JSON, nullable=False)
    actions = Column(JSON, nullable=False)
    case_context_json = Column(Text, nullable=False)
    projected_at = Column(DateTime(timezone=True), nullable=False)


class CredentialLedgerRow(Base):
    """
    G.a: append-only issued credentials.

    ``session_id`` is issuer-internal only — never in portable VC body (I-G-8).
    ``evidence_ref`` is opaque / non-guessable / per-version; independently
    revocable via ``evidence_ref_revoked_at`` without credential ``revoked_at``.
    """

    __tablename__ = "credential_ledger"

    credential_id = Column(String, primary_key=True)
    credential_version = Column(Integer, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    evidence_ref = Column(String, nullable=False, unique=True)
    session_id = Column(String, nullable=False, index=True)
    issued_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    evidence_ref_revoked_at = Column(DateTime(timezone=True), nullable=True)
    projection_digest = Column(String, nullable=False)
    digest_alg = Column(String, nullable=False)
    audit_query_id = Column(String, nullable=False)
    audit_fingerprint = Column(String, nullable=True)
    blueprint_version = Column(String, nullable=False)
    grading_outcome_version = Column(String, nullable=False)
    issuer_key_id = Column(String, nullable=False)
    credential_bytes = Column(Text, nullable=False)


class CredentialStatusListSnapshotRow(Base):
    """G.b: append-only signed status-list snapshots (I-G-5)."""

    __tablename__ = "credential_status_list_snapshot"

    snapshot_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    status_list_schema_version = Column(String, nullable=False)
    key_id = Column(String, nullable=False)
    signed_at = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)
    envelope_bytes = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class StatusListIdentifierRow(Base):
    """I.b: normalized revocation registry — UNIQUE (tenant, kind, id) + closed kind CHECK."""

    __tablename__ = "status_list_identifier"
    __table_args__ = (
        CheckConstraint(
            "identifier_kind IN ('credential', 'regrade', 'arp')",
            name="ck_status_list_identifier_kind",
        ),
    )

    tenant_id = Column(String, primary_key=True)
    identifier_kind = Column(String, primary_key=True)
    identifier_id = Column(String, primary_key=True)
    revoked_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class SessionTranscriptLedgerRow(Base):
    """H.a: append-only sealed transcript SoT — regrade has zero write grant."""

    __tablename__ = "session_transcript_ledger"

    session_id = Column(String, primary_key=True)
    transcript_digest = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    rubric_schema_version = Column(String, nullable=False)
    transcript_bytes = Column(Text, nullable=False)
    sealed_at = Column(DateTime(timezone=True), nullable=False)


class SessionTranscriptProjectionRow(Base):
    """H.a: F-shaped transcript projection — regrade reads this only (I-H-9)."""

    __tablename__ = "session_transcript_projection"
    __table_args__ = ({"schema": RUNTIME_SCHEMA},)

    session_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    transcript_digest = Column(String, nullable=False)
    rubric_schema_version = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)
    projected_at = Column(DateTime(timezone=True), nullable=False)


class RegradeAuditRow(Base):
    """H.a: one row per regrade attempt (I-H-12)."""

    __tablename__ = "regrade_audit"

    regrade_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    caller_kind = Column(String, nullable=False)
    caller_pseudo_id = Column(String, nullable=False)
    scope = Column(String, nullable=False)
    transcript_digest_expected = Column(String, nullable=False)
    transcript_digest_computed = Column(String, nullable=False)
    digest_match = Column(Boolean, nullable=False)
    grader_invoked = Column(Boolean, nullable=False)
    grader_outcome = Column(String, nullable=False)
    regrade_artifact_id = Column(String, nullable=True)
    error_kind = Column(String, nullable=True)
    initiated_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False)


class RegradeArtifactRow(Base):
    """H.a/H.b: portable signed regrade artifact; session_id is internal join only."""

    __tablename__ = "regrade_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "session_id",
            "transcript_digest",
            name="ux_regrade_artifacts_tenant_session_digest",
        ),
    )

    regrade_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    transcript_digest = Column(String, nullable=False)
    grader_version = Column(String, nullable=False)
    rubric_schema_version = Column(String, nullable=False)
    artifact_schema_version = Column(String, nullable=False)
    regrade_issuer_key_id = Column(String, nullable=False)
    signed_at = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)
    envelope_bytes = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class ArpEligibleRubricRow(Base):
    """I.a: published recompute-eligible alternate rubrics (eligibility at grader invoke)."""

    __tablename__ = "arp_recompute_eligible_rubrics"

    tenant_id = Column(String, primary_key=True)
    alternate_rubric_id = Column(String, primary_key=True)
    alternate_rubric_version = Column(String, primary_key=True)
    authority_id = Column(String, nullable=False)
    published = Column(Boolean, nullable=False, default=True)
    recompute_eligible = Column(Boolean, nullable=False, default=True)


class ArpAuditRow(Base):
    """I.a: one row per ARP attempt (I-I-10)."""

    __tablename__ = "arp_audit"

    arp_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    caller_kind = Column(String, nullable=False)
    caller_pseudo_id = Column(String, nullable=False)
    scope = Column(String, nullable=False)
    transcript_digest_expected = Column(String, nullable=False)
    transcript_digest_computed = Column(String, nullable=False)
    digest_match = Column(Boolean, nullable=False)
    alternate_rubric_id = Column(String, nullable=False)
    alternate_rubric_version = Column(String, nullable=True)
    authority_id = Column(String, nullable=True)
    transcript_rubric_schema_version = Column(String, nullable=False)
    sealed_rubric_id = Column(String, nullable=False)
    grader_invoked = Column(Boolean, nullable=False)
    grader_outcome = Column(String, nullable=False)
    arp_artifact_id = Column(String, nullable=True)
    error_kind = Column(String, nullable=True)
    initiated_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False)


class ArpArtifactRow(Base):
    """I.a: portable signed ARP; session_id internal join only."""

    __tablename__ = "arp_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "session_id",
            "transcript_digest",
            "alternate_rubric_id",
            name="ux_arp_artifacts_tenant_session_digest_rubric",
        ),
    )

    arp_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    transcript_digest = Column(String, nullable=False)
    alternate_rubric_id = Column(String, nullable=False)
    alternate_rubric_version = Column(String, nullable=False)
    authority_id = Column(String, nullable=False)
    sealed_rubric_id = Column(String, nullable=False)
    transcript_rubric_schema_version = Column(String, nullable=False)
    grader_version = Column(String, nullable=False)
    artifact_schema_version = Column(String, nullable=False)
    arp_issuer_key_id = Column(String, nullable=False)
    signed_at = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)
    envelope_bytes = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class SamvaadSummativeEvidenceRow(Base):
    """SAMVAAD 021/023 append-only summative evidence source of truth."""

    __tablename__ = "samvaad_summative_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "transcript_digest",
            name="uq_samvaad_summative_tenant_digest",
        ),
        CheckConstraint(
            "assessment_kind = 'summative'",
            name="ck_samvaad_summative_assessment_kind",
        ),
        CheckConstraint(
            "evidence_class IN ('machine_sim', 'preceptor_attested')",
            name="ck_samvaad_summative_evidence_class",
        ),
    )

    evidence_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    learner_pseudo_id = Column(String, nullable=False, index=True)
    session_anchor = Column(String, nullable=False)
    transcript_digest = Column(String, nullable=False)
    grader_version = Column(String, nullable=False)
    rubric_schema_version = Column(String, nullable=False)
    artifact_schema_version = Column(String, nullable=False)
    assessment_kind = Column(String, nullable=False, default="summative")
    evidence_class = Column(String, nullable=False)
    framework_citation_anchor = Column(String, nullable=True)
    competency_hits_json = Column(Text, nullable=False, default="[]")
    freshness_inputs_json = Column(Text, nullable=False, default="{}")
    captured_at_utc = Column(DateTime(timezone=True), nullable=False, index=True)


class SamvaadFormativeEvidenceRow(Base):
    """SAMVAAD 022/023 append-only formative evidence source of truth."""

    __tablename__ = "samvaad_formative_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "evidence_digest",
            name="uq_samvaad_formative_tenant_digest",
        ),
        CheckConstraint(
            "assessment_kind = 'formative'",
            name="ck_samvaad_formative_assessment_kind",
        ),
        CheckConstraint(
            "evidence_class IN "
            "('machine_sim', 'preceptor_attested', 'patient_reported')",
            name="ck_samvaad_formative_evidence_class",
        ),
    )

    evidence_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    learner_pseudo_id = Column(String, nullable=False, index=True)
    assessment_kind = Column(String, nullable=False, default="formative")
    evidence_class = Column(String, nullable=False)
    evidence_digest = Column(String(64), nullable=False)
    source_context_json = Column(Text, nullable=False)
    submitted_by = Column(String, nullable=False)
    session_anchor = Column(String, nullable=False)
    matcher_parameters_json = Column(Text, nullable=False)
    competency_hits_json = Column(Text, nullable=False, default="[]")
    freshness_inputs_json = Column(Text, nullable=False, default="{}")
    captured_at_utc = Column(DateTime(timezone=True), nullable=False, index=True)

