"""Case draft workflow state machine — phase C: approval + distinct principals."""
from __future__ import annotations

from dataclasses import dataclass

from services.pratibimb.authoring.constants import (
    REQUIRED_FIXTURE_KINDS,
    RETIRE_REASON_NOTE_MAX,
    SCOPE_APPROVE,
    SCOPE_APPROVE_SUMMATIVE,
    SCOPE_SUBMIT,
    SUMMATIVE_APPROVALS_REQUIRED,
    ApprovalEventType,
    ApprovalKind,
    CaseWorkflowState,
    FixtureKind,
    PublishPreconditionCode,
    RetirePreconditionCode,
    RetireReasonCode,
    SummativeGrantOutcome,
)
from services.pratibimb.authoring.dry_run import DryRunResult, run_dry_run
from services.pratibimb.authoring.errors import (
    ApproverCannotPublishError,
    AuthorCannotApproveError,
    AlreadyRetiredError,
    DryRunFailedError,
    DryRunHashMismatchError,
    DuplicateApproverError,
    InvalidStateTransitionError,
    InvalidTransitionError,
    MissingFixturesError,
    PhaseNotImplementedError,
    PublishPreconditionFailed,
    PublisherCannotRetireError,
    RetirePreconditionFailed,
    ScopeDeniedError,
    VersionStringBurnedError,
)
from services.pratibimb.authoring.models import (
    ApprovalEventRow,
    CaseDraftRow,
    CaseStateTransitionRow,
    GoldenFixtureRow,
    PublishedCaseVersionRow,
    RetiredCaseVersionRow,
)
from services.pratibimb.authoring.publish import (
    assessment_mode_from_blueprint,
    compile_at_publish,
    fresh_dry_run_at_publish,
    guideline_versions_pinned,
    required_publish_scope,
    structural_validation_errors,
    summative_axes_covered,
    version_string_burned,
)
from services.pratibimb.authoring.registry_snapshot import RegistrySnapshot
from shared.schemas.case_v2 import CorpusTier
from shared.schemas.session import AssessmentMode

IMPLEMENTED_TRANSITIONS = frozenset({
    (CaseWorkflowState.DRAFT, CaseWorkflowState.IN_REVIEW),
    (CaseWorkflowState.IN_REVIEW, CaseWorkflowState.DRAFT),
    (CaseWorkflowState.IN_REVIEW, CaseWorkflowState.APPROVED),
    (CaseWorkflowState.APPROVED, CaseWorkflowState.PUBLISHED),
    (CaseWorkflowState.PUBLISHED, CaseWorkflowState.RETIRED),
})

APPROVAL_TARGET_STATES = frozenset({
    CaseWorkflowState.APPROVED,
    CaseWorkflowState.PUBLISHED,
})


@dataclass(frozen=True)
class TransitionActor:
    subject_id: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class SummativeApprovalResult:
    """Outcome of append-only summative GRANT — never silent."""

    event: ApprovalEventRow
    outcome: SummativeGrantOutcome
    summative_ready: bool


@dataclass(frozen=True)
class PublishResult:
    published: PublishedCaseVersionRow
    transition: CaseStateTransitionRow


@dataclass(frozen=True)
class RetireResult:
    published: PublishedCaseVersionRow
    retired: RetiredCaseVersionRow
    transition: CaseStateTransitionRow


def validate_approver_distinct_from_author(
    *,
    author_subject_id: str,
    actor_subject_id: str,
) -> None:
    validate_author_not_in_approvers(
        author_subject_id=author_subject_id,
        approver_subject_ids=frozenset({actor_subject_id}),
    )


def validate_author_not_in_approvers(
    *,
    author_subject_id: str,
    approver_subject_ids: frozenset[str],
) -> None:
    """author_subject_id NOT IN {approver_1, approver_2, …} — both slots."""
    if author_subject_id in approver_subject_ids:
        raise AuthorCannotApproveError(
            "author cannot act as approver; distinct principals required"
        )


def _required_scope_for(
    from_state: CaseWorkflowState,
    to_state: CaseWorkflowState,
    *,
    assessment_mode: AssessmentMode | None = None,
) -> str | None:
    if (from_state, to_state) == (CaseWorkflowState.IN_REVIEW, CaseWorkflowState.DRAFT):
        return SCOPE_SUBMIT
    if to_state is CaseWorkflowState.IN_REVIEW:
        return SCOPE_SUBMIT
    if to_state is CaseWorkflowState.APPROVED:
        return SCOPE_APPROVE
    if to_state is CaseWorkflowState.PUBLISHED:
        if assessment_mode is AssessmentMode.SUMMATIVE:
            return SCOPE_APPROVE_SUMMATIVE
        return SCOPE_APPROVE
    if to_state is CaseWorkflowState.RETIRED:
        # Same bar as publish: retirement is destructive; formative/summative
        # scopes match the publish gate (not authoring:admin).
        if assessment_mode is AssessmentMode.SUMMATIVE:
            return SCOPE_APPROVE_SUMMATIVE
        return SCOPE_APPROVE
    return None


def active_approval_subject_ids(
    events: list[ApprovalEventRow],
    *,
    kind: ApprovalKind,
) -> frozenset[str]:
    withdrawn = {
        row.supersedes_event_id
        for row in events
        if row.event_type == ApprovalEventType.WITHDRAW.value and row.supersedes_event_id
    }
    return frozenset(
        row.actor_subject_id
        for row in events
        if row.event_type == ApprovalEventType.GRANT.value
        and row.kind == kind.value
        and row.id not in withdrawn
    )


def submit_dry_run_hash(transitions: list[CaseStateTransitionRow]) -> str | None:
    """Most recent DRAFT → IN_REVIEW dry-run hash (submit-time freeze)."""
    for row in reversed(transitions):
        if (
            row.from_state == CaseWorkflowState.DRAFT.value
            and row.to_state == CaseWorkflowState.IN_REVIEW.value
            and row.dry_run_result_hash
        ):
            return row.dry_run_result_hash
    return None


def assert_fresh_dry_run_matches_submit(
    *,
    submit_hash: str | None,
    fresh: DryRunResult,
) -> None:
    if submit_hash is None:
        raise DryRunHashMismatchError("", fresh.result_hash)
    if fresh.result_hash != submit_hash:
        raise DryRunHashMismatchError(submit_hash, fresh.result_hash)


def validate_formative_approver(
    draft: CaseDraftRow,
    actor: TransitionActor,
) -> None:
    validate_approver_distinct_from_author(
        author_subject_id=draft.author_subject_id,
        actor_subject_id=actor.subject_id,
    )
    if SCOPE_APPROVE not in actor.scopes:
        raise ScopeDeniedError(f"missing scope {SCOPE_APPROVE}")
    if SCOPE_APPROVE_SUMMATIVE in actor.scopes and SCOPE_APPROVE not in actor.scopes:
        raise ScopeDeniedError(f"missing scope {SCOPE_APPROVE}")


def validate_summative_approver(
    draft: CaseDraftRow,
    actor: TransitionActor,
    events: list[ApprovalEventRow],
) -> None:
    """
    Distinct-principal rule at the state machine, not the UI.

    author ∉ approvers; approver_1 ≠ approver_2; scope is approve_summative only.
    """
    active = active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE)
    approvers = active | {actor.subject_id}
    validate_author_not_in_approvers(
        author_subject_id=draft.author_subject_id,
        approver_subject_ids=approvers,
    )
    if SCOPE_APPROVE_SUMMATIVE not in actor.scopes:
        raise ScopeDeniedError(f"missing scope {SCOPE_APPROVE_SUMMATIVE}")
    if actor.subject_id in active:
        raise DuplicateApproverError(
            "approver_1_subject_id must differ from approver_2_subject_id"
        )


def validate_transition_request(
    draft: CaseDraftRow,
    *,
    from_state: CaseWorkflowState,
    to_state: CaseWorkflowState,
    actor: TransitionActor,
) -> None:
    current = CaseWorkflowState(draft.current_state)
    if current != from_state:
        raise InvalidTransitionError(
            f"draft is {current.value}, not {from_state.value}"
        )

    if to_state in APPROVAL_TARGET_STATES:
        validate_approver_distinct_from_author(
            author_subject_id=draft.author_subject_id,
            actor_subject_id=actor.subject_id,
        )

    if (from_state, to_state) not in IMPLEMENTED_TRANSITIONS:
        raise PhaseNotImplementedError("phase D")

    required_scope = _required_scope_for(
        from_state,
        to_state,
        assessment_mode=assessment_mode_from_blueprint(draft.blueprint_json)
        if to_state in (CaseWorkflowState.PUBLISHED, CaseWorkflowState.RETIRED)
        else None,
    )
    if required_scope and required_scope not in actor.scopes:
        raise ScopeDeniedError(f"missing scope {required_scope}")


def execute_draft_to_in_review(
    draft: CaseDraftRow,
    fixtures: list[GoldenFixtureRow],
) -> DryRunResult:
    present = {FixtureKind(row.fixture_kind) for row in fixtures}
    missing = REQUIRED_FIXTURE_KINDS - present
    if missing:
        raise MissingFixturesError(
            f"missing golden fixtures: {', '.join(sorted(k.value for k in missing))}"
        )

    result = run_dry_run(draft.blueprint_json, fixtures)
    if not result.passed:
        raise DryRunFailedError("golden fixture dry-run failed")
    return result


def summative_ready(events: list[ApprovalEventRow]) -> bool:
    return (
        len(active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE))
        >= SUMMATIVE_APPROVALS_REQUIRED
    )


def validate_publish_preconditions(
    draft: CaseDraftRow,
    actor: TransitionActor,
    events: list[ApprovalEventRow],
    *,
    fixtures: list[GoldenFixtureRow],
    pinned_snapshot: RegistrySnapshot | None,
    submit_dry_run_hash_value: str | None,
    prior_publishes: list[PublishedCaseVersionRow],
) -> None:
    """
    Fail-closed publish gate. Wrong-state reads ``IMPLEMENTED_TRANSITIONS`` only.
    """
    from_state = CaseWorkflowState(draft.current_state)
    target = CaseWorkflowState.PUBLISHED
    if (from_state, target) not in IMPLEMENTED_TRANSITIONS:
        raise InvalidStateTransitionError(
            from_state=from_state.value,
            to_state=target.value,
            code=PublishPreconditionCode.WRONG_STATE.value,
        )

    mode = assessment_mode_from_blueprint(draft.blueprint_json)
    required_scope = required_publish_scope(mode)
    if required_scope not in actor.scopes:
        raise ScopeDeniedError(f"missing scope {required_scope}")

    try:
        validate_approver_distinct_from_author(
            author_subject_id=draft.author_subject_id,
            actor_subject_id=actor.subject_id,
        )
    except AuthorCannotApproveError as exc:
        raise PublishPreconditionFailed(
            precondition_code=PublishPreconditionCode.AUTHOR_CANNOT_PUBLISH,
            path="actor_subject_id",
            message=str(exc),
        ) from exc

    case_id, version = (
        str((draft.blueprint_json.get("identity") or {}).get("case_id") or ""),
        str((draft.blueprint_json.get("identity") or {}).get("version") or draft.blueprint_version),
    )
    for row in prior_publishes:
        if row.case_id == case_id and row.tenant_id != draft.tenant_id:
            raise PublishPreconditionFailed(
                precondition_code=PublishPreconditionCode.TENANT_CASE_ID_MISMATCH,
                path="tenant_id",
                message=f"case_id {case_id} already published under tenant {row.tenant_id}",
            )
    if version_string_burned(
        tenant_id=draft.tenant_id,
        case_id=case_id,
        version=version,
        prior_rows=prior_publishes,
    ):
        raise VersionStringBurnedError(case_id=case_id, version=version)

    if draft.blueprint_json.get("probe_only"):
        raise PublishPreconditionFailed(
            precondition_code=PublishPreconditionCode.WRONG_STATE,
            path="probe_only",
            message="probe_only drafts cannot be published",
        )

    present = {FixtureKind(row.fixture_kind) for row in fixtures}
    if REQUIRED_FIXTURE_KINDS - present:
        raise PublishPreconditionFailed(
            precondition_code=PublishPreconditionCode.MISSING_FIXTURES,
            path="fixtures",
            message="golden fixture pair is incomplete",
        )

    compile_errors = compile_at_publish(draft.blueprint_json, snapshot=pinned_snapshot)
    if compile_errors is not None:
        raise PublishPreconditionFailed(
            precondition_code=PublishPreconditionCode.COMPILE_FAILED,
            path="grading_blueprint",
            message=compile_errors.errors[0].message,
        )

    for message in structural_validation_errors(draft.blueprint_json):
        code = PublishPreconditionCode.NOT_GOLD_TIER
        if "clinical reviewer" in message:
            code = PublishPreconditionCode.MISSING_CLINICAL_REVIEWER
        raise PublishPreconditionFailed(
            precondition_code=code,
            path="blueprint_json",
            message=message,
        )

    if mode is AssessmentMode.SUMMATIVE:
        identity = draft.blueprint_json.get("identity") or {}
        if str(identity.get("corpus_tier")) != CorpusTier.GOLD.value:
            raise PublishPreconditionFailed(
                precondition_code=PublishPreconditionCode.NOT_GOLD_TIER,
                path="identity.corpus_tier",
                message="summative publish requires GOLD tier",
            )
        if not summative_ready(events):
            raise PublishPreconditionFailed(
                precondition_code=PublishPreconditionCode.SUMMATIVE_NOT_READY,
                path="approval_events",
                message="summative publish requires two active summative GRANTs",
            )
        approvers = active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE)
        if actor.subject_id in approvers:
            raise ApproverCannotPublishError()
        if not guideline_versions_pinned(draft.blueprint_json):
            raise PublishPreconditionFailed(
                precondition_code=PublishPreconditionCode.GUIDELINE_NOT_PINNED,
                path="provenance.guideline_versions",
                message="guideline_versions must be doc_id@sha256:<64-hex>",
            )
        if not summative_axes_covered(draft.blueprint_json):
            raise PublishPreconditionFailed(
                precondition_code=PublishPreconditionCode.SAFETY_AXIS_GAP,
                path="grading_blueprint.hits",
                message="summative publish requires safety-axis coverage",
            )

    try:
        fresh = fresh_dry_run_at_publish(draft, fixtures, snapshot=pinned_snapshot)
    except DryRunFailedError as exc:
        raise PublishPreconditionFailed(
            precondition_code=PublishPreconditionCode.DRY_RUN_FAILED,
            path="fixtures",
            message=str(exc),
        ) from exc

    try:
        assert_fresh_dry_run_matches_submit(
            submit_hash=submit_dry_run_hash_value,
            fresh=fresh,
        )
    except DryRunHashMismatchError as exc:
        raise DryRunHashMismatchError(
            exc.submit_hash,
            exc.fresh_hash,
            phase="publish",
        ) from exc


def validate_retire_preconditions(
    draft: CaseDraftRow,
    actor: TransitionActor,
    *,
    published: PublishedCaseVersionRow | None,
    reason_code: str | None,
    reason_note: str | None = None,
) -> RetireReasonCode:
    """
    Fail-closed retire gate. Wrong-state reads ``IMPLEMENTED_TRANSITIONS`` only.
    Returns the validated ``RetireReasonCode``.
    """
    from_state = CaseWorkflowState(draft.current_state)
    target = CaseWorkflowState.RETIRED
    if (from_state, target) not in IMPLEMENTED_TRANSITIONS:
        raise InvalidStateTransitionError(
            from_state=from_state.value,
            to_state=target.value,
            code=PublishPreconditionCode.WRONG_STATE.value,
        )

    mode = assessment_mode_from_blueprint(draft.blueprint_json)
    required_scope = required_publish_scope(mode)
    if required_scope not in actor.scopes:
        raise ScopeDeniedError(f"missing scope {required_scope}")

    if published is None:
        raise RetirePreconditionFailed(
            precondition_code=RetirePreconditionCode.NO_PUBLISHED_ROW.value,
            path="published_case_versions",
            message="no published_case_versions row for this draft",
        )
    if published.retired_at is not None:
        raise AlreadyRetiredError()
    if actor.subject_id == published.published_by:
        raise PublisherCannotRetireError()

    if reason_code is None or not str(reason_code).strip():
        raise RetirePreconditionFailed(
            precondition_code=RetirePreconditionCode.INVALID_REASON_CODE.value,
            path="reason_code",
            message="reason_code is required",
        )
    try:
        validated = RetireReasonCode(reason_code)
    except ValueError as exc:
        raise RetirePreconditionFailed(
            precondition_code=RetirePreconditionCode.INVALID_REASON_CODE.value,
            path="reason_code",
            message=f"reason_code must be one of {[c.value for c in RetireReasonCode]}",
        ) from exc

    if reason_note is not None and len(reason_note) > RETIRE_REASON_NOTE_MAX:
        raise RetirePreconditionFailed(
            precondition_code=RetirePreconditionCode.REASON_NOTE_TOO_LONG.value,
            path="reason_note",
            message=f"reason_note exceeds {RETIRE_REASON_NOTE_MAX} characters",
        )

    return validated
