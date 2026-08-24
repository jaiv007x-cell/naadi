"""Authoring harness constants — phase A surface."""
from __future__ import annotations

from enum import Enum

from shared.schemas.ledger_read import ConsentScope

HARNESS_VERSION = "0.1.0"
AUTHORING_SCHEMA = "authoring"

RAMESH_DRAFT_ID = "ramesh-kale-stemi-inferior-v1"
RAMESH_CASE_ID = "ramesh_kale.stemi.inferior.v1"
RAMESH_TENANT_ID = "ncvet-default"


class CaseWorkflowState(str, Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class FixtureKind(str, Enum):
    PERFECT_PATH = "perfect_path"
    CRITICAL_MISS = "critical_miss"


REQUIRED_FIXTURE_KINDS = frozenset({FixtureKind.PERFECT_PATH, FixtureKind.CRITICAL_MISS})

# Consent scopes referenced by the state machine (tenant-scoped via store).
SCOPE_SUBMIT = ConsentScope.AUTHORING_SUBMIT.value
SCOPE_APPROVE = ConsentScope.AUTHORING_APPROVE.value
SCOPE_APPROVE_SUMMATIVE = ConsentScope.AUTHORING_APPROVE_SUMMATIVE.value
SCOPE_ADMIN = ConsentScope.AUTHORING_ADMIN.value
SCOPE_READ_PUBLISHED = ConsentScope.AUTHORING_READ_PUBLISHED.value
SCOPE_READ_RETIREMENT_HISTORY = ConsentScope.AUTHORING_READ_RETIREMENT_HISTORY.value

AUTHORING_CONSENT_SCOPES = frozenset({
    ConsentScope.AUTHORING_SUBMIT,
    ConsentScope.AUTHORING_APPROVE,
    ConsentScope.AUTHORING_APPROVE_SUMMATIVE,
    ConsentScope.AUTHORING_ADMIN,
})


class ApprovalKind(str, Enum):
    FORMATIVE = "FORMATIVE"
    SUMMATIVE = "SUMMATIVE"


class ApprovalEventType(str, Enum):
    GRANT = "GRANT"
    WITHDRAW = "WITHDRAW"


class PolicyRejectionCode(str, Enum):
    """
    Public HTTP contract for recoverable authoring policy failures.

    Consumers (UI, CLI, cross-repo integration tests) match on these strings;
    add new codes here — not as bare literals at call sites.
    """

    SCOPE_DENIED = "SCOPE_DENIED"
    AUTHOR_CANNOT_APPROVE = "AUTHOR_CANNOT_APPROVE"
    DRY_RUN_HASH_MISMATCH = "DRY_RUN_HASH_MISMATCH"
    CONTENT_HASH_DRIFT = "CONTENT_HASH_DRIFT"
    DUPLICATE_APPROVER = "DUPLICATE_APPROVER"

    # Phase D: publish / retire
    ALREADY_PUBLISHED = "ALREADY_PUBLISHED"
    ALREADY_RETIRED = "ALREADY_RETIRED"
    VERSION_STRING_BURNED = "VERSION_STRING_BURNED"
    CANNOT_PUBLISH_RETIRED = "CANNOT_PUBLISH_RETIRED"
    RETIRE_REQUIRES_PUBLISHED_STATE = "RETIRE_REQUIRES_PUBLISHED_STATE"
    PUBLISH_PRECONDITION_FAILED = "PUBLISH_PRECONDITION_FAILED"
    RETIRE_PRECONDITION_FAILED = "RETIRE_PRECONDITION_FAILED"
    APPROVER_CANNOT_PUBLISH = "APPROVER_CANNOT_PUBLISH"
    PUBLISHER_CANNOT_RETIRE = "PUBLISHER_CANNOT_RETIRE"

    # Phase E1: ledger finalize vs published blueprint bytes
    BLUEPRINT_HASH_DRIFT = "BLUEPRINT_HASH_DRIFT"
    PUBLISHED_CASE_NOT_FOUND = "PUBLISHED_CASE_NOT_FOUND"
    INVALID_CASE_PICK = "INVALID_CASE_PICK"
    BLUEPRINT_SOURCE_MISMATCH = "BLUEPRINT_SOURCE_MISMATCH"


class SummativeGrantOutcome(str, Enum):
    """
    Explicit outcome for POST /approve-summative — never silent when recorded.

    ``GRANT_RECORDED``: grant appended; summative readiness may have changed.
    ``GRANT_RECORDED_NO_STATE_CHANGE``: grant appended after threshold was
    already met (third+ distinct voucher); audit trail only.
    """

    GRANT_RECORDED = "SUMMATIVE_GRANT_RECORDED"
    GRANT_RECORDED_NO_STATE_CHANGE = "SUMMATIVE_GRANT_RECORDED_NO_STATE_CHANGE"


class StructuralIssueCode(str, Enum):
    """validation_issues ``code`` when ``kind`` is ``structural``."""

    STRUCTURAL = "STRUCTURAL"


class CompileIssueCode(str, Enum):
    """
    validation_issues ``code`` when ``kind`` is ``compile``.

    Wire contract for UI matchers — add codes here, not as bare literals.
    """

    UNKNOWN_MATCHER_KIND = "UNKNOWN_MATCHER_KIND"
    CUSTOM_MATCHER_FORBIDDEN = "CUSTOM_MATCHER_FORBIDDEN"
    UNREGISTERED_ACTION_ID = "UNREGISTERED_ACTION_ID"
    DEPRECATED_ACTION_ID = "DEPRECATED_ACTION_ID"
    NEGATIVE_POINTS = "NEGATIVE_POINTS"
    INVALID_POINTS = "INVALID_POINTS"
    DUPLICATE_HIT_ID = "DUPLICATE_HIT_ID"
    MISSING_HIT_ID = "MISSING_HIT_ID"
    MISSING_MATCHER = "MISSING_MATCHER"
    UNKNOWN_INNER_HIT = "UNKNOWN_INNER_HIT"
    UNKNOWN_SEQUENCE_STEP = "UNKNOWN_SEQUENCE_STEP"


class PublishPreconditionCode(str, Enum):
    """
    Sub-reason for ``PUBLISH_PRECONDITION_FAILED``.

    Kept as an enum so the UI can match without proliferating ad-hoc
    phase-specific strings.
    """

    WRONG_STATE = "WRONG_STATE"
    COMPILE_FAILED = "COMPILE_FAILED"
    CONTENT_HASH_DRIFT = "CONTENT_HASH_DRIFT"
    DRY_RUN_HASH_MISMATCH = "DRY_RUN_HASH_MISMATCH"
    DRY_RUN_FAILED = "DRY_RUN_FAILED"
    SUMMATIVE_NOT_READY = "SUMMATIVE_NOT_READY"
    MISSING_CLINICAL_REVIEWER = "MISSING_CLINICAL_REVIEWER"
    NOT_GOLD_TIER = "NOT_GOLD_TIER"
    GUIDELINE_NOT_PINNED = "GUIDELINE_NOT_PINNED"
    MISSING_FIXTURES = "MISSING_FIXTURES"
    SAFETY_AXIS_GAP = "SAFETY_AXIS_GAP"
    AUTHOR_CANNOT_PUBLISH = "AUTHOR_CANNOT_PUBLISH"
    APPROVER_CANNOT_PUBLISH = "APPROVER_CANNOT_PUBLISH"
    TENANT_CASE_ID_MISMATCH = "TENANT_CASE_ID_MISMATCH"
    VERSION_STRING_BURNED = "VERSION_STRING_BURNED"


class RetireReasonCode(str, Enum):
    CLINICAL_ERROR = "clinical_error"
    SUPERSEDED_BY_NEW_VERSION = "superseded_by_new_version"
    GUIDELINE_CHANGE = "guideline_change"
    POLICY_CHANGE = "policy_change"
    OPERATOR_REQUEST = "operator_request"


class RetirePreconditionCode(str, Enum):
    """Sub-reason for ``RETIRE_PRECONDITION_FAILED``."""

    WRONG_STATE = "WRONG_STATE"
    INVALID_REASON_CODE = "INVALID_REASON_CODE"
    REASON_NOTE_TOO_LONG = "REASON_NOTE_TOO_LONG"
    PUBLISHER_CANNOT_RETIRE = "PUBLISHER_CANNOT_RETIRE"
    ALREADY_RETIRED = "ALREADY_RETIRED"
    NO_PUBLISHED_ROW = "NO_PUBLISHED_ROW"


RETIRE_REASON_NOTE_MAX = 256
SUMMATIVE_APPROVALS_REQUIRED = 2
