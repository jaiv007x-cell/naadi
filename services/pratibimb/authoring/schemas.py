"""Pydantic models for the authoring HTTP API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.pratibimb.authoring.constants import FixtureKind, RetireReasonCode


class CreateDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blueprint_json: dict[str, Any]
    blueprint_version: str = Field(min_length=1)


class UpdateDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blueprint_json: dict[str, Any]
    blueprint_version: str = Field(min_length=1)


class UpsertFixtureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_kind: FixtureKind
    trace_json: dict[str, Any]
    expected_grade_json: dict[str, Any]


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = None


class WithdrawApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grant_id: str = Field(min_length=1)
    reason: Optional[str] = None


class RetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: RetireReasonCode
    reason_note: Optional[str] = Field(default=None, max_length=256)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["structural", "compile"]
    code: str
    path: str
    message: str


class FixtureView(BaseModel):
    model_config = ConfigDict(frozen=True)
    fixture_kind: str
    content_hash: str


class TransitionView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    from_state: str
    to_state: str
    actor_subject_id: str
    actor_scope: str
    reason: Optional[str]
    dry_run_result_hash: Optional[str]
    content_hash_snapshot: Optional[str]
    validation_context_hash: Optional[str]
    occurred_at: datetime


class ApprovalEventView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    event_type: str
    kind: str
    actor_subject_id: str
    actor_scope: str
    dry_run_result_hash: Optional[str]
    submit_dry_run_hash: Optional[str]
    content_hash: str
    validation_context_hash: Optional[str] = None
    reason: Optional[str]
    supersedes_event_id: Optional[str]
    occurred_at: datetime


class DryRunOutcomeView(BaseModel):
    model_config = ConfigDict(frozen=True)
    fixture_kind: str
    expected_passed: bool
    actual_passed: bool
    matched_expectation: bool


class DryRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    passed: bool
    result_hash: str
    outcomes: list[DryRunOutcomeView]


class PublishedCaseVersionView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    draft_id: str
    tenant_id: str
    case_id: str
    version: str
    assessment_mode: str
    content_hash: str
    harness_version: str
    published_at: datetime
    published_by: str
    clinical_reviewer: Optional[str] = None
    retired_at: Optional[datetime] = None
    retired_by: Optional[str] = None
    retired_reason_code: Optional[str] = None
    retired_reason_note: Optional[str] = None


class RetiredCaseVersionView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    published_case_version_id: str
    draft_id: str
    tenant_id: str
    case_id: str
    version: str
    retired_at: datetime
    retired_by: str
    reason_code: str
    reason_note: Optional[str] = None


class PublishedCaseVersionsPage(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: list[PublishedCaseVersionView]
    next_cursor: Optional[str] = None


class RetirementHistoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: list[RetiredCaseVersionView]
    next_cursor: Optional[str] = None


class DraftSummaryView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    tenant_id: str
    author_subject_id: str
    blueprint_version: str
    current_state: str
    content_hash: str
    validation_errors: list[str]
    validation_issues: list[ValidationIssue]
    case_id: str
    case_version: str
    harness_version: str
    created_at: datetime
    updated_at: datetime


class DraftDetailView(DraftSummaryView):
    model_config = ConfigDict(frozen=True)
    blueprint_json: dict[str, Any]
    fixtures: list[FixtureView]
    transitions: list[TransitionView]
    approvals: list[ApprovalEventView]
    summative_ready: bool
    published: Optional[PublishedCaseVersionView] = None
    retired: Optional[RetiredCaseVersionView] = None


class SummativeApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: ApprovalEventView
    outcome: str
    summative_ready: bool


class PublishResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    draft: DraftDetailView
    published: PublishedCaseVersionView


class RetireResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    draft: DraftDetailView
    published: PublishedCaseVersionView
    retired: RetiredCaseVersionView
