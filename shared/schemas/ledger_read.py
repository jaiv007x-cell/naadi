"""
Read-side Pydantic schemas for the ledger. Consumed by BEEMA dashboards,
Skill Decay model, and preceptor review UI.

`superseded_by` is reserved from day one for the future revision-chain
schema — always None in MVP, but present so consumers don't have to
re-parse when preceptor amendments land.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ConsentScope(str, Enum):
    SELF_LEARNER = "self.learner"
    PRECEPTOR_REVIEW = "preceptor.review"
    AGGREGATE_ANALYTICS = "aggregate.analytics"
    RESEARCH_DEIDENTIFIED = "research.deidentified"
    AUTHORING_SUBMIT = "authoring:submit"
    AUTHORING_APPROVE = "authoring:approve"
    AUTHORING_APPROVE_SUMMATIVE = "authoring:approve_summative"
    AUTHORING_ADMIN = "authoring:admin"
    # E3: audited catalog reads (split — retirement is higher privilege)
    AUTHORING_READ_PUBLISHED = "authoring:read_published"
    AUTHORING_READ_RETIREMENT_HISTORY = "authoring:read_retirement_history"
    # F.a: NCVET regulator session evidence (split from list in F.b)
    NCVET_READ_SESSION_EVIDENCE = "ncvet:read_session_evidence"
    # F.b: NCVET regulator learner session list (split from detail in F.a)
    NCVET_READ_LEARNER_SESSIONS = "ncvet:read_learner_sessions"
    # G.a: credential issuance (split from evidence-ref fetch)
    NCVET_ISSUE_CREDENTIAL = "ncvet:issue_credential"
    # G.a: resolve opaque evidence_ref — lookup-by-ref discipline in the name
    NCVET_FETCH_EVIDENCE_BY_REF = "ncvet:fetch_evidence_by_ref"
    # G.b: verify / status-list / revoke — zero implication across G.a scopes
    NCVET_VERIFY_CREDENTIAL = "ncvet:verify_credential"
    NCVET_FETCH_STATUS_LIST = "ncvet:fetch_status_list"
    NCVET_REVOKE_CREDENTIAL = "ncvet:revoke_credential"
    # H.a: recompute-under-same-rubric (zero implication vs G scopes)
    NCVET_REGRADE_SESSION = "ncvet:regrade_session"
    # H.b: verify/present portable regrade envelope — zero implication vs regrade_session
    NCVET_VERIFY_REGRADE = "ncvet:verify_regrade"
    # I.a: alternate-rubric recompute (zero implication vs H/G scopes)
    NCVET_RECOMPUTE_ALTERNATE_RUBRIC = "ncvet:recompute_alternate_rubric"
    # I.b: verify/present ARP — zero implication vs recompute
    NCVET_VERIFY_ARP = "ncvet:verify_arp"


class EvidenceEventView(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    session_id: str
    learner_pseudo_id: str
    cohort_id: str
    case_id: str
    physio_engine_version: str
    grade_total_normalized: float = Field(ge=0.0, le=1.0)
    grade_passed: bool
    recorded_at: datetime
    # Trace integrity (replay). Distinct from blueprint_content_hash.
    content_hash: str
    # Phase E1: written at ledger append from the session create-time stamp.
    # NULL for pre-E1 / seed sessions. Never resolved at view-read time.
    blueprint_content_hash: Optional[str] = None
    # Phase E2.2.c: write-time provenance of blueprint_content_hash.
    # NULL = pre-E2.2.c legacy. Never resolved at view-read time.
    blueprint_source: Optional[str] = None
    flags: list[dict]
    evidence: list[dict]
    superseded_by: Optional[str] = None


class LearnerEvidenceFilter(BaseModel):
    learner_pseudo_id: str
    case_id: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    cause_prefix: Optional[str] = None
    include_superseded: bool = False


class LearnerEvidenceResponse(BaseModel):
    learner_pseudo_id: str
    events: list[EvidenceEventView]
    total: int = Field(ge=0)


class CohortFilter(BaseModel):
    unit_id: Optional[str] = None
    cohort_id: Optional[str] = None
    tenant_id: Optional[str] = None
    window_days: int = Field(default=30, ge=1, le=365)
    cause_prefix: Optional[str] = None


class QueryFilters(BaseModel):
    unit_id: Optional[str] = None
    role: Optional[str] = None
    cohort_id: Optional[str] = None
    case_id: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    cause_prefix: Optional[str] = None


class UnitSafetyReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    unit_id: str
    window_days: int
    cohort_size: int = Field(ge=50)
    dp_noise_epsilon: Optional[float] = None
    critical_violation_rate: float = Field(ge=0.0, le=1.0)
    top_causes: list[dict]
    generated_at: datetime


class SkillDecayCurve(BaseModel):
    model_config = ConfigDict(frozen=True)

    competency_id: str
    role: str
    cohort_size: int = Field(ge=50)
    points: list[dict]
    half_life_days: Optional[float] = None
    generated_at: datetime


class AggregatePatternReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    filters: QueryFilters
    cohort_size: int = Field(ge=50)
    dp_noise_epsilon: Optional[float] = None
    patterns: list[dict]
    generated_at: datetime


class SessionSummaryView(BaseModel):
    """Lightweight session row for ledger session listings."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    case_id: str
    cohort_id: str
    grade_total_normalized: float = Field(ge=0.0, le=1.0)
    grade_passed: bool
    recorded_at: datetime
    content_hash: str
    learner_pseudo_id: Optional[str] = None


class CohortSummaryReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    cohort_id: str
    cohort_size: int = Field(ge=50)
    critical_violation_rate: float = Field(ge=0.0, le=1.0)
    patterns: list[dict]
    generated_at: datetime


class PrivacyErrorBody(BaseModel):
    error: str
    reason: str
    minimum_required: Optional[int] = None


class NcvetSessionEvidenceView(BaseModel):
    """F.a: graded session evidence + redacted case context (I-F-5)."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    tenant_id: str
    learner_pseudo_id: str
    cohort_id: str
    case_id: str
    case_version: str
    finalized_at_utc: datetime
    physio_engine_version: str
    rubric_version: str
    replay_hash: str
    blueprint_content_hash: Optional[str] = None
    blueprint_source: Optional[str] = None
    grade_total: float
    grade_passed: bool
    axis_normalized: dict
    evidence: list[dict]
    flags: list[dict]
    actions: list[dict]
    case_context: dict
    schema_version: str = "ncvet_session_evidence.v1"


class NcvetLearnerSessionSummary(BaseModel):
    """F.b: session summary for list — no case_context blob (detail via F.a)."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    tenant_id: str
    learner_pseudo_id: str
    cohort_id: str
    case_id: str
    case_version: str
    finalized_at_utc: datetime
    grade_total: float
    grade_passed: bool
    replay_hash: str
    blueprint_content_hash: Optional[str] = None
    blueprint_source: Optional[str] = None
    schema_version: str = "ncvet_learner_session_summary.v1"


class NcvetLearnerSessionsPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[NcvetLearnerSessionSummary]
    next_cursor: Optional[str] = None
