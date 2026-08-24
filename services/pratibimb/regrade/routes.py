"""FastAPI routes for ``/v1/regrade/`` (H.a + H.b + H.c Shape A)."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.api.deps import get_auth_context
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.deps import get_consent_store_for_authoring
from services.pratibimb.ledger.db import get_engine
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.consent_store import ConsentGrantStore
from services.pratibimb.ledger_read.deps import get_audit_sink
from services.pratibimb.ledger_read.errors import ScopeDeniedError, TenantBoundaryError
from services.pratibimb.ledger_read.request_context import get_request_id
from services.pratibimb.regrade.gateway import JwtRegradeGateway
from services.pratibimb.regrade.service import (
    RegradeArtifactNotFoundError,
    RegradeDuplicateError,
    RegradeService,
    TranscriptDigestMismatchError,
    TranscriptProjectionNotFoundError,
)

router = APIRouter(prefix="/v1/regrade", tags=["regrade"])


class VerifyRegradeRequest(BaseModel):
    envelope: dict[str, Any] = Field(...)


def _audit_unavailable_http(exc: AuditWriteError) -> HTTPException:
    correlation_id = getattr(exc, "correlation_id", None) or get_request_id() or str(
        uuid.uuid4()
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "audit_unavailable", "correlation_id": correlation_id},
    )


def get_regrade_gateway(
    consent_store: Annotated[ConsentGrantStore, Depends(get_consent_store_for_authoring)],
) -> JwtRegradeGateway:
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return JwtRegradeGateway(
        RegradeService(SessionLocal),
        ConsentResolver(consent_store),
        audit_sink=get_audit_sink(),
    )


@router.post("/session/{session_id}")
async def regrade_session(
    session_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtRegradeGateway, Depends(get_regrade_gateway)],
) -> dict[str, Any]:
    try:
        return await gateway.regrade(auth, session_id)
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except ScopeDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "consent_scope"},
        ) from exc
    except TenantBoundaryError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "tenant_mismatch"},
        ) from exc
    except TranscriptProjectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found"},
        ) from exc
    except TranscriptDigestMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "transcript_digest_mismatch",
                "error_kind": "transcript_digest_mismatch",
                "regrade_id": exc.regrade_id,
            },
        ) from exc
    except RegradeDuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "regrade_duplicate",
                "error_kind": "regrade_duplicate",
                "regrade_id": exc.regrade_id,
            },
        ) from exc


@router.post("/verify")
async def verify_regrade_assist(
    body: VerifyRegradeRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtRegradeGateway, Depends(get_regrade_gateway)],
) -> dict[str, Any]:
    try:
        result = await gateway.verify_assist(
            auth,
            body.envelope,
            request_id=get_request_id(),
        )
        return {"accepted": result.accepted, "reason": result.reason}
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except ScopeDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "consent_scope"},
        ) from exc


@router.get("/artifacts/{regrade_id}")
async def fetch_regrade_artifact(
    regrade_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtRegradeGateway, Depends(get_regrade_gateway)],
) -> dict[str, Any]:
    try:
        return await gateway.fetch_artifact(
            auth,
            regrade_id,
            request_id=get_request_id(),
        )
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except ScopeDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "consent_scope"},
        ) from exc
    except RegradeArtifactNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found"},
        ) from exc
