"""HTTP surface for the authoring harness."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.blueprint_io import content_hash_from_case_json
from services.pratibimb.authoring.compiler import (
    draft_validation_errors,
    draft_validation_issues,
)
from services.pratibimb.authoring.constants import CaseWorkflowState, PolicyRejectionCode
from services.pratibimb.authoring.deps import (
    authoring_db,
    get_authoring_actor,
    get_authoring_store,
)
from services.pratibimb.authoring.dry_run import run_dry_run
from services.pratibimb.authoring.errors import (
    AuthoringError,
    BlueprintValidationError,
    ContentHashDriftError,
    DryRunFailedError,
    DryRunHashMismatchError,
    InvalidStateTransitionError,
    InvalidTransitionError,
    MissingFixturesError,
    PhaseNotImplementedError,
    PolicyRejected,
    PublishPreconditionFailed,
    RetirePreconditionFailed,
    ScopeDeniedError,
)
from services.pratibimb.authoring.models import (
    ApprovalEventRow,
    CaseDraftRow,
    PublishedCaseVersionRow,
    RetiredCaseVersionRow,
)
from services.pratibimb.authoring.schemas import (
    ApprovalEventView,
    CreateDraftRequest,
    DraftDetailView,
    DraftSummaryView,
    DryRunOutcomeView,
    DryRunResponse,
    FixtureView,
    PublishResponse,
    PublishedCaseVersionView,
    RetireRequest,
    RetireResponse,
    RetiredCaseVersionView,
    SummativeApprovalResponse,
    TransitionRequest,
    TransitionView,
    UpdateDraftRequest,
    UpsertFixtureRequest,
    ValidationIssue,
    WithdrawApprovalRequest,
)
from services.pratibimb.authoring.state_machine import TransitionActor, summative_ready
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.ledger.corpus_facade import project_after_authoring_commit
from services.pratibimb.ledger_read.deps import get_auth_context

router = APIRouter(prefix="/v1/authoring", tags=["authoring"])


def _policy_errors(exc: PolicyRejected) -> list[dict[str, str]]:
    return [
        {"code": error.code.value, "path": error.path, "message": error.message}
        for error in exc.errors
    ]


def _authoring_http(exc: Exception) -> HTTPException:
    if isinstance(exc, ScopeDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": PolicyRejectionCode.SCOPE_DENIED.value,
                "message": str(exc),
                "errors": _policy_errors(exc),
            },
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
                "error": exc.code.value,
                "message": str(exc),
                "submit_hash": exc.submit_hash,
                "fresh_hash": exc.fresh_hash,
                "errors": _policy_errors(exc),
            },
        )
    if isinstance(exc, ContentHashDriftError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": exc.code.value,
                "message": str(exc),
                "submit_hash": exc.submit_hash,
                "current_hash": exc.current_hash,
                "errors": _policy_errors(exc),
            },
        )
    if isinstance(exc, (PublishPreconditionFailed, RetirePreconditionFailed)):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": exc.code.value,
                "precondition": str(exc.precondition_code),
                "message": str(exc),
                "errors": _policy_errors(exc),
            },
        )
    if isinstance(exc, PolicyRejected):
        forbidden = {
            PolicyRejectionCode.AUTHOR_CANNOT_APPROVE,
            PolicyRejectionCode.DUPLICATE_APPROVER,
            PolicyRejectionCode.APPROVER_CANNOT_PUBLISH,
            PolicyRejectionCode.PUBLISHER_CANNOT_RETIRE,
        }
        http_status = (
            status.HTTP_403_FORBIDDEN
            if exc.code in forbidden
            else status.HTTP_409_CONFLICT
        )
        return HTTPException(
            status_code=http_status,
            detail={
                "error": exc.code.value,
                "message": str(exc),
                "errors": _policy_errors(exc),
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
    if isinstance(exc, InvalidTransitionError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "INVALID_TRANSITION", "message": str(exc)},
        )
    if isinstance(exc, PhaseNotImplementedError):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": "PHASE_NOT_IMPLEMENTED", "message": str(exc)},
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")


def _validation_snapshot(
    store: CaseDraftStore | None,
    draft: CaseDraftRow,
):
    if store is None or draft.current_state == CaseWorkflowState.DRAFT.value:
        return None
    return store.pinned_registry_snapshot(draft.id)


def _summary_view(
    draft: CaseDraftRow,
    *,
    store: CaseDraftStore | None = None,
) -> DraftSummaryView:
    identity = draft.blueprint_json.get("identity") or {}
    snapshot = _validation_snapshot(store, draft)
    issues = draft_validation_issues(draft.blueprint_json, snapshot=snapshot)
    return DraftSummaryView(
        id=draft.id,
        tenant_id=draft.tenant_id,
        author_subject_id=draft.author_subject_id,
        blueprint_version=draft.blueprint_version,
        current_state=draft.current_state,
        content_hash=content_hash_from_case_json(draft.blueprint_json),
        validation_errors=draft_validation_errors(
            draft.blueprint_json,
            snapshot=snapshot,
        ),
        validation_issues=[ValidationIssue(**issue) for issue in issues],
        case_id=str(identity.get("case_id") or ""),
        case_version=str(identity.get("version") or draft.blueprint_version),
        harness_version=draft.harness_version,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _transition_view(row) -> TransitionView:
    return TransitionView(
        id=row.id,
        from_state=row.from_state,
        to_state=row.to_state,
        actor_subject_id=row.actor_subject_id,
        actor_scope=row.actor_scope,
        reason=row.reason,
        dry_run_result_hash=row.dry_run_result_hash,
        content_hash_snapshot=row.content_hash_snapshot,
        validation_context_hash=row.validation_context_hash,
        occurred_at=row.occurred_at,
    )


def _approval_view(row: ApprovalEventRow) -> ApprovalEventView:
    return ApprovalEventView(
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


def _published_view(row: PublishedCaseVersionRow) -> PublishedCaseVersionView:
    return PublishedCaseVersionView(
        id=row.id,
        draft_id=row.draft_id,
        tenant_id=row.tenant_id,
        case_id=row.case_id,
        version=row.version,
        assessment_mode=row.assessment_mode,
        content_hash=row.content_hash,
        harness_version=row.harness_version,
        published_at=row.published_at,
        published_by=row.published_by,
        clinical_reviewer=row.clinical_reviewer,
        retired_at=row.retired_at,
        retired_by=row.retired_by,
        retired_reason_code=row.retired_reason_code,
        retired_reason_note=row.retired_reason_text,
    )


def _retired_view(row: RetiredCaseVersionRow) -> RetiredCaseVersionView:
    return RetiredCaseVersionView(
        id=row.id,
        published_case_version_id=row.published_case_version_id,
        draft_id=row.draft_id,
        tenant_id=row.tenant_id,
        case_id=row.case_id,
        version=row.version,
        retired_at=row.retired_at,
        retired_by=row.retired_by,
        reason_code=row.reason_code,
        reason_note=row.reason_text,
    )


def _detail_view(store: CaseDraftStore, draft: CaseDraftRow) -> DraftDetailView:
    approvals = store.list_approval_events(draft.id)
    published = store.get_published_for_draft(draft.id)
    retired_rows = store.list_retired_for_draft(draft.id)
    return DraftDetailView(
        **_summary_view(draft, store=store).model_dump(),
        blueprint_json=draft.blueprint_json,
        fixtures=[
            FixtureView(fixture_kind=row.fixture_kind, content_hash=row.content_hash)
            for row in store.list_fixtures(draft.id)
        ],
        transitions=[_transition_view(row) for row in store.list_transitions(draft.id)],
        approvals=[_approval_view(row) for row in approvals],
        summative_ready=summative_ready(approvals),
        published=_published_view(published) if published is not None else None,
        retired=_retired_view(retired_rows[-1]) if retired_rows else None,
    )


def _draft_or_404(
    draft_id: str,
    auth: AuthContext,
    store: CaseDraftStore,
) -> CaseDraftRow:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    _require_tenant_draft(draft, auth)
    return draft


@router.get("/drafts", response_model=list[DraftSummaryView])
async def list_drafts(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
) -> list[DraftSummaryView]:
    return [
        _summary_view(draft, store=store)
        for draft in store.list_drafts_for_tenant(auth.tenant_id)
    ]


@router.post(
    "/drafts",
    response_model=DraftDetailView,
    status_code=status.HTTP_201_CREATED,
)
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
    return _detail_view(store, _draft_or_404(draft_id, auth, store))


@router.put("/drafts/{draft_id}", response_model=DraftDetailView)
async def update_draft(
    draft_id: str,
    body: UpdateDraftRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    session: Annotated[Session, Depends(authoring_db)],
) -> DraftDetailView:
    _draft_or_404(draft_id, auth, store)
    try:
        draft = store.update_draft(
            draft_id,
            blueprint_json=body.blueprint_json,
            blueprint_version=body.blueprint_version,
        )
    except Exception as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    return _detail_view(store, draft)


@router.put("/drafts/{draft_id}/fixtures", response_model=FixtureView)
async def upsert_fixture(
    draft_id: str,
    body: UpsertFixtureRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    session: Annotated[Session, Depends(authoring_db)],
) -> FixtureView:
    _draft_or_404(draft_id, auth, store)
    try:
        row = store.upsert_fixture(
            draft_id,
            fixture_kind=body.fixture_kind,
            trace_json=body.trace_json,
            expected_grade_json=body.expected_grade_json,
        )
    except Exception as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    return FixtureView(fixture_kind=row.fixture_kind, content_hash=row.content_hash)


@router.post("/drafts/{draft_id}/dry-run", response_model=DryRunResponse)
async def dry_run_draft(
    draft_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
) -> DryRunResponse:
    draft = _draft_or_404(draft_id, auth, store)
    fixtures = store.list_fixtures(draft_id)
    if not fixtures:
        raise _authoring_http(MissingFixturesError("no fixtures attached"))
    if draft.blueprint_json.get("grading_blueprint") is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="draft has no grading_blueprint; dry-run requires rubric",
        )
    try:
        result = run_dry_run(draft.blueprint_json, fixtures)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DryRunResponse(
        passed=result.passed,
        result_hash=result.result_hash,
        outcomes=[
            DryRunOutcomeView(
                fixture_kind=outcome.fixture_kind.value,
                expected_passed=outcome.expected_passed,
                actual_passed=outcome.actual_passed,
                matched_expectation=outcome.matched_expectation,
            )
            for outcome in result.outcomes
        ],
    )


async def _transition(
    draft_id: str,
    *,
    to_state: CaseWorkflowState,
    reason: str | None,
    auth: AuthContext,
    store: CaseDraftStore,
    actor: TransitionActor,
    session: Session,
) -> DraftDetailView:
    _draft_or_404(draft_id, auth, store)
    try:
        store.transition(draft_id, to_state=to_state, actor=actor, reason=reason)
    except Exception as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    refreshed = store.get_draft(draft_id)
    assert refreshed is not None
    return _detail_view(store, refreshed)


@router.post("/drafts/{draft_id}/submit", response_model=DraftDetailView)
async def submit_for_review(
    draft_id: str,
    body: TransitionRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    session: Annotated[Session, Depends(authoring_db)],
) -> DraftDetailView:
    return await _transition(
        draft_id,
        to_state=CaseWorkflowState.IN_REVIEW,
        reason=body.reason,
        auth=auth,
        store=store,
        actor=actor,
        session=session,
    )


@router.post("/drafts/{draft_id}/revert", response_model=DraftDetailView)
async def revert_to_draft(
    draft_id: str,
    body: TransitionRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    session: Annotated[Session, Depends(authoring_db)],
) -> DraftDetailView:
    return await _transition(
        draft_id,
        to_state=CaseWorkflowState.DRAFT,
        reason=body.reason,
        auth=auth,
        store=store,
        actor=actor,
        session=session,
    )


@router.post("/drafts/{draft_id}/approve", response_model=DraftDetailView)
async def approve_draft(
    draft_id: str,
    body: TransitionRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    session: Annotated[Session, Depends(authoring_db)],
) -> DraftDetailView:
    return await _transition(
        draft_id,
        to_state=CaseWorkflowState.APPROVED,
        reason=body.reason,
        auth=auth,
        store=store,
        actor=actor,
        session=session,
    )


@router.post(
    "/drafts/{draft_id}/approve-summative",
    response_model=SummativeApprovalResponse,
)
async def approve_summative(
    draft_id: str,
    body: TransitionRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    session: Annotated[Session, Depends(authoring_db)],
) -> SummativeApprovalResponse:
    _draft_or_404(draft_id, auth, store)
    try:
        result = store.record_summative_approval(
            draft_id,
            actor=actor,
            reason=body.reason,
        )
    except Exception as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    return SummativeApprovalResponse(
        event=_approval_view(result.event),
        outcome=result.outcome.value,
        summative_ready=result.summative_ready,
    )


@router.post("/drafts/{draft_id}/withdraw-approval", response_model=DraftDetailView)
async def withdraw_approval(
    draft_id: str,
    body: WithdrawApprovalRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    session: Annotated[Session, Depends(authoring_db)],
) -> DraftDetailView:
    _draft_or_404(draft_id, auth, store)
    try:
        store.withdraw_approval(
            draft_id,
            grant_id=body.grant_id,
            actor=actor,
            reason=body.reason,
        )
    except Exception as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    refreshed = store.get_draft(draft_id)
    assert refreshed is not None
    return _detail_view(store, refreshed)


@router.post("/drafts/{draft_id}/publish", response_model=PublishResponse)
async def publish_draft(
    draft_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    session: Annotated[Session, Depends(authoring_db)],
) -> PublishResponse:
    _draft_or_404(draft_id, auth, store)
    try:
        result = store.publish_draft(draft_id, actor=actor)
    except Exception as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    project_after_authoring_commit(
        result.published.id,
        authoring_session=session,
        tenant_id=auth.tenant_id,
    )
    refreshed = store.get_draft(draft_id)
    assert refreshed is not None
    return PublishResponse(
        draft=_detail_view(store, refreshed),
        published=_published_view(result.published),
    )


@router.post("/drafts/{draft_id}/retire", response_model=RetireResponse)
async def retire_draft(
    draft_id: str,
    body: RetireRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    actor: Annotated[TransitionActor, Depends(get_authoring_actor)],
    session: Annotated[Session, Depends(authoring_db)],
) -> RetireResponse:
    _draft_or_404(draft_id, auth, store)
    try:
        result = store.retire_published(
            draft_id,
            actor=actor,
            reason_code=body.reason_code.value,
            reason_note=body.reason_note,
        )
    except Exception as exc:
        raise _authoring_http(exc) from exc
    session.commit()
    project_after_authoring_commit(
        result.published.id,
        authoring_session=session,
        tenant_id=auth.tenant_id,
    )
    refreshed = store.get_draft(draft_id)
    assert refreshed is not None
    return RetireResponse(
        draft=_detail_view(store, refreshed),
        published=_published_view(result.published),
        retired=_retired_view(result.retired),
    )


@router.get("/published/{case_id}/{version}", include_in_schema=False)
async def legacy_published_version(case_id: str, version: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/v1/ledger/published_case_versions/{case_id}/{version}",
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    )


@router.get("/published", include_in_schema=False)
async def legacy_published_versions(
    case_id: Annotated[str, Query(min_length=1)],
    include_retired: bool = True,
) -> RedirectResponse:
    retired = str(include_retired).lower()
    return RedirectResponse(
        url=(
            "/v1/ledger/published_case_versions"
            f"?case_id={case_id}&include_retired={retired}"
        ),
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    )
