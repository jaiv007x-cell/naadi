"""FastAPI routes for ``/v1/arp/`` (I.a mint + I.b verify/fetch)."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.api.deps import get_auth_context
from services.pratibimb.arp.gateway import JwtArpGateway
from services.pratibimb.arp.service import (
    ArpArtifactNotFoundError,
    ArpDigestMismatchError,
    ArpDuplicateError,
    ArpRubricIdentityCollisionError,
    ArpRubricNotEligibleError,
    ArpService,
    TranscriptDigestMismatchError,
    TranscriptProjectionNotFoundError,
)
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.deps import get_consent_store_for_authoring
from services.pratibimb.ledger.db import get_engine
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.consent_store import ConsentGrantStore
from services.pratibimb.ledger_read.deps import get_audit_sink
from services.pratibimb.ledger_read.errors import ScopeDeniedError, TenantBoundaryError
from services.pratibimb.ledger_read.request_context import get_request_id

router = APIRouter(prefix="/v1/arp", tags=["arp"])


class ArpRecomputeRequest(BaseModel):
    alternate_rubric_id: str = Field(..., min_length=1)
    alternate_rubric_version: str | None = None
    regrade_id: str | None = None
    credential_id: str | None = None


class VerifyArpRequest(BaseModel):
    envelope: dict[str, Any] = Field(...)
    status_list: dict[str, Any] | None = None


def _audit_unavailable_http(exc: AuditWriteError) -> HTTPException:
    correlation_id = getattr(exc, "correlation_id", None) or get_request_id() or str(
        uuid.uuid4()
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "audit_unavailable", "correlation_id": correlation_id},
    )


def get_arp_gateway(
    consent_store: Annotated[ConsentGrantStore, Depends(get_consent_store_for_authoring)],
) -> JwtArpGateway:
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return JwtArpGateway(
        ArpService(SessionLocal),
        ConsentResolver(consent_store),
        audit_sink=get_audit_sink(),
    )


@router.post("/session/{session_id}")
async def arp_recompute_session(
    session_id: str,
    body: ArpRecomputeRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtArpGateway, Depends(get_arp_gateway)],
) -> dict[str, Any]:
    try:
        return await gateway.recompute(
            auth,
            session_id,
            alternate_rubric_id=body.alternate_rubric_id,
            alternate_rubric_version=body.alternate_rubric_version,
            regrade_id_cite=body.regrade_id,
            credential_id_cite=body.credential_id,
        )
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
                "arp_id": exc.arp_id,
            },
        ) from exc
    except ArpRubricIdentityCollisionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "arp_rubric_identity_collision",
                "error_kind": "arp_rubric_identity_collision",
                "arp_id": exc.arp_id,
            },
        ) from exc
    except ArpRubricNotEligibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": exc.error_kind,
                "error_kind": exc.error_kind,
                "arp_id": exc.arp_id,
            },
        ) from exc
    except ArpDuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "arp_duplicate",
                "error_kind": "arp_duplicate",
                "arp_id": exc.arp_id,
            },
        ) from exc


@router.post("/verify")
async def verify_arp_assist(
    body: VerifyArpRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtArpGateway, Depends(get_arp_gateway)],
) -> dict[str, Any]:
    try:
        result = await gateway.verify_assist(
            auth,
            body.envelope,
            request_id=get_request_id(),
            status_list=body.status_list,
        )
        return result.as_body()
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except ScopeDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "consent_scope"},
        ) from exc
    except ArpDigestMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "digest_mismatch",
                "error_kind": "digest_mismatch",
                "arp_id": exc.arp_id,
                "presented_digest": exc.presented_digest,
                "stored_digest": exc.stored_digest,
            },
        ) from exc


@router.get("/artifacts/{arp_id}")
async def fetch_arp_artifact(
    arp_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtArpGateway, Depends(get_arp_gateway)],
) -> dict[str, Any]:
    try:
        return await gateway.fetch_artifact(
            auth,
            arp_id,
            request_id=get_request_id(),
        )
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except ScopeDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "consent_scope"},
        ) from exc
    except ArpArtifactNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found"},
        ) from exc
