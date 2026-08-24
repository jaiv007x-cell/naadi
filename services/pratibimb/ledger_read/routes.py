"""HTTP read surface for the evidence ledger (GET + JWT Bearer)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from services.pratibimb.app.api.deps import AuthContext, get_auth_context, get_jwt_gateway
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.authoring.deps import get_authoring_store, get_consent_store_for_authoring
from services.pratibimb.authoring.schemas import (
    PublishedCaseVersionView,
    PublishedCaseVersionsPage,
    RetiredCaseVersionView,
    RetirementHistoryPage,
)
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.catalog_gateway import JwtAuthoringCatalogGateway
from services.pratibimb.ledger_read.catalog_service import (
    AuthoringCatalogReadService,
    CATALOG_PAGE_MAX,
)
from services.pratibimb.ledger_read.deps import get_audit_sink, ledger_reader
from services.pratibimb.ledger_read.errors import (
    InsufficientCohortError,
    InvalidCatalogCursorError,
    InvalidNcvetCursorError,
    ScopeDeniedError,
    TenantBoundaryError,
)
from services.pratibimb.ledger_read.factory import (
    build_audited_catalog_read_service,
    build_audited_ncvet_read_service,
)
from services.pratibimb.ledger_read.jwt_gateway import JwtLedgerGateway
from services.pratibimb.ledger_read.consent_store import ConsentGrantStore
from services.pratibimb.ledger_read.ncvet_cursor import NCVET_LIST_PAGE_MAX
from services.pratibimb.ledger_read.ncvet_gateway import JwtNcvetGateway
from services.pratibimb.ledger_read.ncvet_service import (
    NcvetEvidenceReadService,
    SessionEvidenceNotFoundError,
)
from services.pratibimb.ledger_read.request_context import get_request_id
from services.pratibimb.ledger.reader import SqlLedgerReader
from shared.schemas.ledger_read import (
    CohortSummaryReport,
    EvidenceEventView,
    LearnerEvidenceFilter,
    NcvetLearnerSessionsPage,
    NcvetSessionEvidenceView,
    SessionSummaryView,
    UnitSafetyReport,
)

router = APIRouter(prefix="/v1/ledger", tags=["ledger-read"])


def _privacy_http(exc: Exception) -> HTTPException:
    if isinstance(exc, ScopeDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "scope_denied",
                "required_scopes": [s.value for s in exc.required],
                "granted_scopes": [s.value for s in exc.granted],
            },
        )
    if isinstance(exc, TenantBoundaryError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "tenant_mismatch",
                "requested_tenant": exc.requested,
                "auth_tenant": exc.auth_tenant,
            },
        )
    if isinstance(exc, InsufficientCohortError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "insufficient_cohort",
                "reason": "minimum_cohort_size_not_met",
                "minimum_required": exc.minimum_required,
            },
        )
    raise exc


def _audit_unavailable_http(exc: AuditWriteError) -> HTTPException:
    """I-E3-9: stable 503 envelope for fail-closed catalog audit failures.

    Used for retirement history (E3.b.1+) and for **any page** of a paginated
    catalog read (E3.b.2): audit failure on page N returns this shape — no
    partial rows, no cursor in the body. The client's opaque cursor remains
    valid for retry because it is self-contained.

    Fields:
    - ``error``: always ``audit_unavailable`` (machine-parseable)
    - ``correlation_id``: request correlation for sink log grep
    - ``retry_after_seconds``: optional backoff hint when sink failure is transient
    """
    correlation_id = exc.correlation_id or get_request_id() or str(uuid.uuid4())
    detail: dict = {
        "error": "audit_unavailable",
        "correlation_id": correlation_id,
    }
    if exc.retry_after_seconds is not None:
        detail["retry_after_seconds"] = exc.retry_after_seconds
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


def _cursor_invalid_http(exc: InvalidCatalogCursorError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": "cursor_invalid"},
    )


def _ncvet_cursor_invalid_http(exc: InvalidNcvetCursorError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": "cursor_invalid"},
    )


def get_catalog_gateway(
    store: Annotated[CaseDraftStore, Depends(get_authoring_store)],
    consent_store: Annotated[ConsentGrantStore, Depends(get_consent_store_for_authoring)],
) -> JwtAuthoringCatalogGateway:
    audited = build_audited_catalog_read_service(
        AuthoringCatalogReadService(store),
        audit_sink=get_audit_sink(),
    )
    return JwtAuthoringCatalogGateway(audited, ConsentResolver(consent_store))


def get_ncvet_gateway(
    reader: Annotated[SqlLedgerReader, Depends(ledger_reader)],
    consent_store: Annotated[ConsentGrantStore, Depends(get_consent_store_for_authoring)],
) -> JwtNcvetGateway:
    audited = build_audited_ncvet_read_service(
        NcvetEvidenceReadService(reader._s),
        audit_sink=get_audit_sink(),
    )
    return JwtNcvetGateway(audited, ConsentResolver(consent_store))


@router.get(
    "/session_evidence/{session_id}",
    response_model=NcvetSessionEvidenceView,
)
async def get_session_evidence(
    session_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtNcvetGateway, Depends(get_ncvet_gateway)],
    tenant_id: Annotated[Optional[str], Query()] = None,
) -> NcvetSessionEvidenceView:
    """Fail-closed NCVET read — audit sink failure → I-E3-9 ``503`` envelope."""
    try:
        return await gateway.get_session_evidence(
            auth,
            session_id,
            tenant_id=tenant_id,
        )
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc
    except SessionEvidenceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session evidence not found",
        ) from exc


@router.get("/learner_sessions", response_model=NcvetLearnerSessionsPage)
async def list_learner_sessions(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtNcvetGateway, Depends(get_ncvet_gateway)],
    learner_pseudo_id: Annotated[str, Query(min_length=1)],
    tenant_id: Annotated[Optional[str], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=NCVET_LIST_PAGE_MAX)] = NCVET_LIST_PAGE_MAX,
    cursor: Annotated[Optional[str], Query()] = None,
) -> NcvetLearnerSessionsPage:
    """
    Fail-closed NCVET paginated read (I-F-6, I-F-7).

    Audit sink failure on **any page** (including page 2+) returns I-E3-9
    ``503`` envelope: ``error=audit_unavailable``, optional
    ``retry_after_seconds``, ``correlation_id`` - no partial ``items``, no
    ``next_cursor`` in body.

    Cursor is opaque server state derived from DB keyset. Audit sink failure
    does not invalidate a cursor; client may retry the same cursor after recovery.
    """
    try:
        page = await gateway.list_learner_sessions(
            auth,
            learner_pseudo_id,
            tenant_id=tenant_id,
            limit=limit,
            cursor=cursor,
        )
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc
    except InvalidNcvetCursorError as exc:
        raise _ncvet_cursor_invalid_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_request", "message": str(exc)},
        ) from exc
    return NcvetLearnerSessionsPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/published_case_versions", response_model=PublishedCaseVersionsPage)
async def list_published_case_versions(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtAuthoringCatalogGateway, Depends(get_catalog_gateway)],
    case_id: Annotated[str, Query(min_length=1)],
    include_retired: Annotated[bool, Query()] = True,
    tenant_id: Annotated[Optional[str], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=CATALOG_PAGE_MAX)] = CATALOG_PAGE_MAX,
    cursor: Annotated[Optional[str], Query()] = None,
) -> PublishedCaseVersionsPage:
    try:
        page = await gateway.list_published_case_versions(
            auth,
            case_id=case_id,
            include_retired=include_retired,
            tenant_id=tenant_id,
            limit=limit,
            cursor=cursor,
        )
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc
    except InvalidCatalogCursorError as exc:
        raise _cursor_invalid_http(exc) from exc
    return PublishedCaseVersionsPage(items=page.items, next_cursor=page.next_cursor)


@router.get(
    "/published_case_versions/{case_id}/{version}",
    response_model=PublishedCaseVersionView,
)
async def get_published_case_version(
    case_id: str,
    version: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtAuthoringCatalogGateway, Depends(get_catalog_gateway)],
    tenant_id: Annotated[Optional[str], Query()] = None,
) -> PublishedCaseVersionView:
    try:
        row = await gateway.get_published_case_version(
            auth,
            case_id=case_id,
            version=version,
            tenant_id=tenant_id,
        )
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="published version not found",
        )
    return row


@router.get("/retirement_history", response_model=RetirementHistoryPage)
async def list_retirement_history(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtAuthoringCatalogGateway, Depends(get_catalog_gateway)],
    case_id: Annotated[Optional[str], Query(min_length=1)] = None,
    tenant_id: Annotated[Optional[str], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=CATALOG_PAGE_MAX)] = CATALOG_PAGE_MAX,
    cursor: Annotated[Optional[str], Query()] = None,
) -> RetirementHistoryPage:
    """Fail-closed catalog read — audit sink failure → I-E3-9 ``503`` envelope."""
    try:
        page = await gateway.list_retirement_history(
            auth,
            case_id=case_id,
            tenant_id=tenant_id,
            limit=limit,
            cursor=cursor,
        )
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc
    except InvalidCatalogCursorError as exc:
        raise _cursor_invalid_http(exc) from exc
    return RetirementHistoryPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/sessions", response_model=list[SessionSummaryView])
async def list_sessions(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtLedgerGateway, Depends(get_jwt_gateway)],
    cohort_id: Optional[str] = Query(default=None),
    learner_pseudo_id: Optional[str] = Query(default=None),
    case_id: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[SessionSummaryView]:
    try:
        if learner_pseudo_id:
            events = await gateway.list_evidence_events(
                auth,
                LearnerEvidenceFilter(
                    learner_pseudo_id=learner_pseudo_id,
                    case_id=case_id,
                    since=since,
                    until=until,
                ),
            )
            return [
                SessionSummaryView(
                    session_id=e.session_id,
                    case_id=e.case_id,
                    cohort_id=e.cohort_id,
                    grade_total_normalized=e.grade_total_normalized,
                    grade_passed=e.grade_passed,
                    recorded_at=e.recorded_at,
                    content_hash=e.content_hash,
                    learner_pseudo_id=e.learner_pseudo_id,
                )
                for e in events[:limit]
            ]
        if cohort_id:
            return await gateway.list_cohort_sessions(
                auth,
                cohort_id=cohort_id,
                case_id=case_id,
                since=since,
                until=until,
                limit=limit,
            )
        raise HTTPException(
            status_code=400,
            detail={"error": "cohort_id or learner_pseudo_id required"},
        )
    except (ScopeDeniedError, InsufficientCohortError) as exc:
        raise _privacy_http(exc) from exc


@router.get("/learners/{pseudo_id}/evidence", response_model=list[EvidenceEventView])
async def learner_evidence(
    pseudo_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtLedgerGateway, Depends(get_jwt_gateway)],
    case_id: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    cause_prefix: Optional[str] = Query(default=None),
) -> list[EvidenceEventView]:
    try:
        return await gateway.list_evidence_events(
            auth,
            LearnerEvidenceFilter(
                learner_pseudo_id=pseudo_id,
                case_id=case_id,
                since=since,
                until=until,
                cause_prefix=cause_prefix,
            ),
        )
    except (ScopeDeniedError, InsufficientCohortError) as exc:
        raise _privacy_http(exc) from exc


@router.get("/cohorts/{cohort_id}/summary", response_model=CohortSummaryReport)
async def cohort_summary(
    cohort_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtLedgerGateway, Depends(get_jwt_gateway)],
    cause_prefix: Optional[str] = Query(default=None),
) -> CohortSummaryReport:
    try:
        return await gateway.cohort_summary(
            auth, cohort_id, cause_prefix=cause_prefix
        )
    except (ScopeDeniedError, InsufficientCohortError) as exc:
        raise _privacy_http(exc) from exc


@router.get("/units/{unit_id}/safety", response_model=UnitSafetyReport)
async def unit_safety(
    unit_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtLedgerGateway, Depends(get_jwt_gateway)],
    window_days: int = Query(default=30, ge=1, le=365),
) -> UnitSafetyReport:
    from shared.schemas.ledger_read import CohortFilter

    try:
        return await gateway.unit_safety_report(
            auth,
            CohortFilter(unit_id=unit_id, window_days=window_days),
        )
    except (ScopeDeniedError, InsufficientCohortError) as exc:
        raise _privacy_http(exc) from exc
