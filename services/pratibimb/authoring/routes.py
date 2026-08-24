"""HTTP surface for the authoring harness — phase A."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.blueprint_io import (
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
    validation_errors_from_case_json,
)
from services.pratibimb.authoring.constants import CaseWorkflowState
from services.pratibimb.authoring.deps import (
    authoring_db,
    get_authoring_actor,
    get_authoring_store,
from services.pratibimb.authoring.blueprint_io import content_hash_from_case_json
from services.pratibimb.authoring.compiler import (
    draft_validation_errors,
    draft_validation_issues,
)
    MissingFixturesError,
from services.pratibimb.authoring.constants import CaseWorkflowState, PolicyRejectionCode
    ScopeDeniedError,
)
from services.pratibimb.authoring.models import CaseDraftRow
from services.pratibimb.authoring.schemas import (
    CreateDraftRequest,
    DraftDetailView,
    DraftSummaryView,
    DryRunOutcomeView,
    DryRunResponse,
    FixtureView,
    TransitionRequest,
    TransitionView,
    UpdateDraftRequest,
    UpsertFixtureRequest,
)
    AlreadyPublishedError,

    AlreadyRetiredError,

    ApproverCannotPublishError,

    AuthorCannotApproveError,

    AuthoringError,

    BlueprintValidationError,

    ContentHashDriftError,

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
    if isinstance(exc, (InvalidTransitionError, DryRunFailedError, MissingFixturesError)):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": type(exc).__name__, "message": str(exc)},
        )
    if isinstance(exc, AuthorCannotApproveError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "author_cannot_approve", "message": str(exc)},
        )
    if isinstance(exc, PhaseNotImplementedError):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": "phase_not_implemented", "message": str(exc)},
        )
    if isinstance(exc, KeyError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AuthoringError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": type(exc).__name__, "message": str(exc)},
        )
    raise exc


def _require_tenant_draft(draft: CaseDraftRow, auth: AuthContext) -> None:
    if draft.tenant_id != auth.tenant_id:
    ApprovalEventView,

    ValidationIssue,

    WithdrawApprovalRequest,

    SummativeApprovalResponse,

    PublishResponse,

    PublishedCaseVersionView,

    RetireRequest,

    RetireResponse,

    RetiredCaseVersionView,

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")

from services.pratibimb.authoring.state_machine import TransitionActor, summative_ready
def _summary_view(draft: CaseDraftRow) -> DraftSummaryView:
    identity = draft.blueprint_json.get("identity") or {}
from services.pratibimb.ledger.corpus_facade import project_after_authoring_commit
    return DraftSummaryView(
        id=draft.id,
        tenant_id=draft.tenant_id,
        author_subject_id=draft.author_subject_id,
        blueprint_version=draft.blueprint_version,
        current_state=draft.current_state,
        content_hash=content_hash_from_case_json(draft.blueprint_json),
        validation_errors=validation_errors_from_case_json(draft.blueprint_json),
        case_id=str(identity.get("case_id") or ""),
        case_version=str(identity.get("version") or draft.blueprint_version),
        harness_version=draft.harness_version,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _detail_view(store: CaseDraftStore, draft: CaseDraftRow) -> DraftDetailView:
    summary = _summary_view(draft)
    fixtures = [
            detail={"error": PolicyRejectionCode.SCOPE_DENIED.value, "message": str(exc)},

        )

    if isinstance(exc, MissingFixturesError):

        return HTTPException(

            status_code=status.HTTP_409_CONFLICT,

            detail={"error": "MISSING_FIXTURES", "message": str(exc)},

        )

    if isinstance(exc, DryRunFailedError):

        return HTTPException(

            status_code=status.HTTP_409_CONFLICT,

            detail={"error": "DRY_RUN_FAILED", "message": str(exc)},

        )

    if isinstance(exc, DryRunHashMismatchError):

        return HTTPException(

            status_code=status.HTTP_409_CONFLICT,

            detail={
                "error": PolicyRejectionCode.DRY_RUN_HASH_MISMATCH.value,
                "message": str(exc),
                "submit_hash": exc.submit_hash,
                "fresh_hash": exc.fresh_hash,
                "errors": [
                    {"code": e.code.value, "path": e.path, "message": e.message}
                    for e in exc.errors
                ],
            },

        )

    if isinstance(exc, ContentHashDriftError):

        return HTTPException(

            status_code=status.HTTP_409_CONFLICT,

            detail={
                "error": PolicyRejectionCode.CONTENT_HASH_DRIFT.value,
                "message": str(exc),
                "submit_hash": exc.submit_hash,
                "current_hash": exc.current_hash,
                "errors": [
                    {"code": e.code.value, "path": e.path, "message": e.message}
                    for e in exc.errors
                ],
            },

        )

    if isinstance(exc, DuplicateApproverError):

        return HTTPException(

            status_code=status.HTTP_403_FORBIDDEN,

            detail={"error": PolicyRejectionCode.DUPLICATE_APPROVER.value, "message": str(exc)},

        )

    if isinstance(exc, InvalidTransitionError):
            id=row.id,
            from_state=row.from_state,
            to_state=row.to_state,
            actor_subject_id=row.actor_subject_id,
            actor_scope=row.actor_scope,
            reason=row.reason,
            dry_run_result_hash=row.dry_run_result_hash,
            occurred_at=row.occurred_at,
        )
    if isinstance(exc, BlueprintValidationError):

        return HTTPException(

            status_code=status.HTTP_409_CONFLICT,

            detail={

                "error": "BLUEPRINT_INVALID",

                "message": str(exc),

                "validation_errors": list(exc.errors),

            },

        )

        for row in store.list_transitions(draft.id)
    ]
    return DraftDetailView(
        **summary.model_dump(),
        blueprint_json=draft.blueprint_json,
        fixtures=fixtures,
            detail={"error": PolicyRejectionCode.AUTHOR_CANNOT_APPROVE.value, "message": str(exc)},
    )


    if isinstance(exc, ApproverCannotPublishError):

        return HTTPException(

            status_code=status.HTTP_403_FORBIDDEN,

            detail={
                "error": PolicyRejectionCode.APPROVER_CANNOT_PUBLISH.value,
                "message": str(exc),
            },

        )

    if isinstance(exc, PublisherCannotRetireError):

        return HTTPException(

            status_code=status.HTTP_403_FORBIDDEN,

            detail={
                "error": PolicyRejectionCode.PUBLISHER_CANNOT_RETIRE.value,
                "message": str(exc),
            },

        )

    if isinstance(exc, VersionStringBurnedError):

        return HTTPException(

            status_code=status.HTTP_409_CONFLICT,

            detail={
                "error": PolicyRejectionCode.VERSION_STRING_BURNED.value,
                "message": str(exc),
                "errors": [
                    {"code": e.code.value, "path": e.path, "message": e.message}
                    for e in exc.errors
                ],
            },

        )

    if isinstance(exc, (AlreadyPublishedError, AlreadyRetiredError)):

        return HTTPException(

            status_code=status.HTTP_409_CONFLICT,

            detail={
                "error": exc.code.value,
                "message": str(exc),
            },

        )

    if isinstance(exc, PublishPreconditionFailed):

        return HTTPException(

            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,

            detail={
                "error": PolicyRejectionCode.PUBLISH_PRECONDITION_FAILED.value,
                "precondition": exc.precondition_code.value
                if hasattr(exc.precondition_code, "value")
                else str(exc.precondition_code),
                "message": str(exc),
                "errors": [
                    {"code": e.code.value, "path": e.path, "message": e.message}
                    for e in exc.errors
                ],
            },

        )

    if isinstance(exc, RetirePreconditionFailed):

        return HTTPException(

            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,

            detail={
                "error": PolicyRejectionCode.RETIRE_PRECONDITION_FAILED.value,
                "precondition": exc.precondition_code,
                "message": str(exc),
                "errors": [
                    {"code": e.code.value, "path": e.path, "message": e.message}
                    for e in exc.errors
                ],
            },

        )

    if isinstance(exc, InvalidStateTransitionError):

        return HTTPException(

            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,

            detail={
                "error": exc.code,
                "from_state": exc.from_state,
                "to_state": exc.to_state,
                "message": str(exc),
            },

        )

@router.get("/drafts", response_model=list[DraftSummaryView])
async def list_drafts(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
) -> list[DraftSummaryView]:
    drafts = store.list_drafts_for_tenant(auth.tenant_id)
    return [_summary_view(d) for d in drafts]


@router.post("/drafts", response_model=DraftDetailView, status_code=status.HTTP_201_CREATED)
async def create_draft(
    body: CreateDraftRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    session: Annotated[Session, Depends(authoring_db)],
) -> DraftDetailView:
    draft = store.create_draft(
        tenant_id=auth.tenant_id,
        author_subject_id=auth.subject_pseudo_id,
        blueprint_json=body.blueprint_json,
        blueprint_version=body.blueprint_version,
    )
    session.commit()
    return _detail_view(store, draft)


@router.get("/drafts/{draft_id}", response_model=DraftDetailView)
async def get_draft(
    draft_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
) -> DraftDetailView:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    _require_tenant_draft(draft, auth)
    return _detail_view(store, draft)


@router.put("/drafts/{draft_id}", response_model=DraftDetailView)
def _summary_view(
    draft: CaseDraftRow,
    *,
    store: CaseDraftStore | None = None,
) -> DraftSummaryView:

    identity = draft.blueprint_json.get("identity") or {}

    snapshot = None
    if store is not None and draft.current_state in {
        CaseWorkflowState.IN_REVIEW.value,
        CaseWorkflowState.APPROVED.value,
    }:
        snapshot = store.pinned_registry_snapshot(draft.id)

    return DraftSummaryView(

        id=draft.id,

        tenant_id=draft.tenant_id,

        author_subject_id=draft.author_subject_id,

        blueprint_version=draft.blueprint_version,

        current_state=draft.current_state,

        content_hash=content_hash_from_case_json(draft.blueprint_json),

        validation_errors=draft_validation_errors(draft.blueprint_json, snapshot=snapshot),

        validation_issues=[
            ValidationIssue(**issue)
            for issue in draft_validation_issues(draft.blueprint_json, snapshot=snapshot)
        ],

    return _detail_view(store, draft)


@router.put("/drafts/{draft_id}/fixtures", response_model=FixtureView)
async def upsert_fixture(
    draft_id: str,
    body: UpsertFixtureRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    session: Annotated[Session, Depends(authoring_db)],
) -> FixtureView:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    _require_tenant_draft(draft, auth)
    if draft.current_state != CaseWorkflowState.DRAFT.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
    summary = _summary_view(draft, store=store)
        )
    row = store.upsert_fixture(
        draft_id,
        fixture_kind=body.fixture_kind,
        trace_json=body.trace_json,
        expected_grade_json=body.expected_grade_json,
    )
    session.commit()
    return FixtureView(fixture_kind=row.fixture_kind, content_hash=row.content_hash)


@router.post("/drafts/{draft_id}/dry-run", response_model=DryRunResponse)
async def dry_run_draft(
    draft_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
) -> DryRunResponse:
    """
    Grade golden fixtures through Nirikshak without ledger writes.

    Per-request dry-run only — does not toggle process-wide LEDGER_DISABLED.
    """
    draft = store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    _require_tenant_draft(draft, auth)
    fixtures = store.list_fixtures(draft_id)
            content_hash_snapshot=row.content_hash_snapshot,

            validation_context_hash=row.validation_context_hash,

    if not fixtures:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "missing_fixtures", "message": "no fixtures attached"},
        )
    try:
        if draft.blueprint_json.get("grading_blueprint") is None:
            raise HTTPException(
    approvals = [

        ApprovalEventView(

            id=row.id,

            event_type=row.event_type,

            kind=row.kind,

            actor_subject_id=row.actor_subject_id,

            actor_scope=row.actor_scope,

            dry_run_result_hash=row.dry_run_result_hash,

            submit_dry_run_hash=row.submit_dry_run_hash,

            content_hash=row.content_hash,

            validation_context_hash=row.validation_context_hash,

            reason=row.reason,

            supersedes_event_id=row.supersedes_event_id,

            occurred_at=row.occurred_at,

        )

        for row in store.list_approval_events(draft.id)

    ]

    return DraftDetailView(

        **summary.model_dump(),

        blueprint_json=draft.blueprint_json,

        fixtures=fixtures,

        transitions=transitions,

        approvals=approvals,
        outcomes=[
        summative_ready=summative_ready(store.list_approval_events(draft.id)),

        published=_published_view(pub) if (pub := store.get_published_for_draft(draft.id)) else None,

        retired=_retired_view(ret[0]) if (ret := store.list_retired_for_draft(draft.id)) else None,

            DryRunOutcomeView(
                fixture_kind=o.fixture_kind.value,
                expected_passed=o.expected_passed,
                actual_passed=o.actual_passed,
                matched_expectation=o.matched_expectation,
            )
            for o in result.outcomes
        ],
    )


@router.post("/drafts/{draft_id}/submit", response_model=DraftDetailView)
async def submit_for_review(
    draft_id: str,
    body: TransitionRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    return [_summary_view(d, store=store) for d in drafts]
) -> DraftDetailView:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    _require_tenant_draft(draft, auth)
    try:
        store.transition(
            draft_id,
            to_state=CaseWorkflowState.IN_REVIEW,
            actor=actor,
            reason=body.reason,
        )
    except AuthoringError as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    refreshed = store.get_draft(draft_id)
    assert refreshed is not None
    return _detail_view(store, refreshed)


@router.post("/drafts/{draft_id}/revert", response_model=DraftDetailView)
async def revert_to_draft(
    draft_id: str,
    body: TransitionRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    session: Annotated[Session, Depends(authoring_db)],
) -> DraftDetailView:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    _require_tenant_draft(draft, auth)
    try:
        store.transition(
            draft_id,
            to_state=CaseWorkflowState.DRAFT,
            actor=actor,
            reason=body.reason,
        )
    except AuthoringError as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    refreshed = store.get_draft(draft_id)
    assert refreshed is not None
    return _detail_view(store, refreshed)

