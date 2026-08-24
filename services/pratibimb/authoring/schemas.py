"""Pydantic models for the authoring HTTP API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from typing import Any, Literal, Optional


class CreateDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blueprint_json: dict[str, Any]
    blueprint_version: str = Field(min_length=1)
from services.pratibimb.authoring.constants import FixtureKind, RetireReasonCode

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


class DraftSummaryView(BaseModel):
    model_config = ConfigDict(frozen=True)
class WithdrawApprovalRequest(BaseModel):

    model_config = ConfigDict(extra="forbid")

    grant_id: str = Field(min_length=1)
    reason: Optional[str] = None





class ValidationIssue(BaseModel):

    model_config = ConfigDict(frozen=True)

    kind: Literal["structural", "compile"]
    code: str
    path: str
    message: str





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






    id: str
    tenant_id: str
    author_subject_id: str
    blueprint_version: str
    current_state: str
    content_hash: str
    validation_errors: list[str]
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

