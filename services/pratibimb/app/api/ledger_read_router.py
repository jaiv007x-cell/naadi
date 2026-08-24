"""HTTP POST surface for LedgerReadService. Privacy enforcement in gateway + policy."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from services.pratibimb.app.api.deps import AuthContext, get_auth_context, get_jwt_gateway
from services.pratibimb.ledger_read.errors import InsufficientCohortError, ScopeDeniedError
from services.pratibimb.ledger_read.jwt_gateway import JwtLedgerGateway
from shared.schemas.ledger_read import (
    AggregatePatternReport,
    CohortFilter,
    LearnerEvidenceFilter,
    LearnerEvidenceResponse,
    UnitSafetyReport,
)

router = APIRouter(prefix="/v1/ledger", tags=["ledger_read"])


def _handle_privacy_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ScopeDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "scope_denied",
                "required_scopes": [s.value for s in exc.required],
                "granted_scopes": [s.value for s in exc.granted],
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
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"error": "internal", "reason": type(exc).__name__},
    )


@router.post("/evidence/learner", response_model=LearnerEvidenceResponse)
async def post_learner_evidence(
    filt: LearnerEvidenceFilter,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtLedgerGateway, Depends(get_jwt_gateway)],
) -> LearnerEvidenceResponse:
    try:
        return await gateway.learner_evidence(auth, filt)
    except (ScopeDeniedError, InsufficientCohortError) as exc:
        raise _handle_privacy_errors(exc) from exc


@router.post("/reports/unit_safety", response_model=UnitSafetyReport)
async def post_unit_safety_report(
    filt: CohortFilter,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtLedgerGateway, Depends(get_jwt_gateway)],
) -> UnitSafetyReport:
    try:
        return await gateway.unit_safety_report(auth, filt)
    except (ScopeDeniedError, InsufficientCohortError) as exc:
        raise _handle_privacy_errors(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc


@router.post("/reports/aggregate_patterns", response_model=AggregatePatternReport)
async def post_aggregate_patterns(
    filt: CohortFilter,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtLedgerGateway, Depends(get_jwt_gateway)],
) -> AggregatePatternReport:
    try:
        return await gateway.aggregate_patterns(auth, filt)
    except (ScopeDeniedError, InsufficientCohortError) as exc:
        raise _handle_privacy_errors(exc) from exc
