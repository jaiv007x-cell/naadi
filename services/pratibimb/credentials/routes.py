"""FastAPI routes for ``/v1/credentials/`` (G.a + G.b) — separate from ``/v1/ledger/``."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.api.deps import get_auth_context
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.deps import get_consent_store_for_authoring
from services.pratibimb.credentials.gateway import JwtCredentialGateway
from services.pratibimb.credentials.service import (
    CredentialIssuerService,
    EvidenceRefNotFoundError,
    EvidenceRefRevokedError,
)
from services.pratibimb.credentials.sign import DEFAULT_ISSUER_KEY_ID
from services.pratibimb.credentials.status_service import (
    CredentialAlreadyRevokedError,
    CredentialNotFoundError,
    CredentialStatusService,
)
from services.pratibimb.credentials.verify import KeyRecord
from services.pratibimb.ledger.db import get_engine
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.consent_store import ConsentGrantStore
from services.pratibimb.ledger_read.deps import get_audit_sink, ledger_reader
from services.pratibimb.ledger_read.errors import ScopeDeniedError, TenantBoundaryError
from services.pratibimb.ledger_read.factory import build_audited_ncvet_read_service
from services.pratibimb.ledger_read.ncvet_service import (
    NcvetEvidenceReadService,
    SessionEvidenceNotFoundError,
)
from services.pratibimb.ledger_read.request_context import get_request_id

router = APIRouter(prefix="/v1/credentials", tags=["credentials"])


class IssueCredentialRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


class RevokeCredentialRequest(BaseModel):
    credential_id: str = Field(..., min_length=1)


class VerifyCredentialRequest(BaseModel):
    credential: dict[str, Any]
    status_list: dict[str, Any]
    option_b_staleness: bool = False
    option_b_reason: str = ""


def _audit_unavailable_http(exc: AuditWriteError) -> HTTPException:
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


def _privacy_http(exc: Exception) -> HTTPException:
    if isinstance(exc, ScopeDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "consent_scope"},
        )
    if isinstance(exc, TenantBoundaryError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "tenant_mismatch"},
        )
    raise exc


def get_credential_gateway(
    reader: Annotated[SqlLedgerReader, Depends(ledger_reader)],
    consent_store: Annotated[ConsentGrantStore, Depends(get_consent_store_for_authoring)],
) -> JwtCredentialGateway:
    audited = build_audited_ncvet_read_service(
        NcvetEvidenceReadService(reader._s),
        audit_sink=get_audit_sink(),
    )
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    service = CredentialIssuerService(audited, SessionLocal)
    status_svc = CredentialStatusService(SessionLocal, get_audit_sink())
    keyring = {DEFAULT_ISSUER_KEY_ID: KeyRecord(key_id=DEFAULT_ISSUER_KEY_ID)}
    return JwtCredentialGateway(
        service,
        ConsentResolver(consent_store),
        audited_ncvet_for_denials=audited.with_caller_kind("ncvet_issuer"),
        status_service=status_svc,
        keyring=keyring,
    )


@router.post("/issue")
async def issue_credential(
    body: IssueCredentialRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtCredentialGateway, Depends(get_credential_gateway)],
) -> dict[str, Any]:
    try:
        return await gateway.issue(
            auth,
            body.session_id,
            request_id=get_request_id(),
        )
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc
    except SessionEvidenceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found"},
        ) from exc


@router.get("/evidence/{evidence_ref}")
async def fetch_evidence_by_ref(
    evidence_ref: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtCredentialGateway, Depends(get_credential_gateway)],
) -> dict[str, Any]:
    try:
        view = await gateway.fetch_evidence_by_ref(
            auth,
            evidence_ref,
            request_id=get_request_id(),
        )
        return view.model_dump(mode="json")
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc
    except EvidenceRefRevokedError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "evidence_ref_not_found", "error_kind": "ref_revoked"},
        ) from exc
    except EvidenceRefNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "evidence_ref_not_found", "error_kind": "not_found"},
        ) from exc
    except SessionEvidenceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found"},
        ) from exc


@router.post("/revoke")
async def revoke_credential(
    body: RevokeCredentialRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtCredentialGateway, Depends(get_credential_gateway)],
) -> dict[str, Any]:
    try:
        return await gateway.revoke(auth, body.credential_id)
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc
    except CredentialAlreadyRevokedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "already_revoked"},
        ) from exc
    except CredentialNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found"},
        ) from exc


@router.get("/status_list")
async def fetch_status_list(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtCredentialGateway, Depends(get_credential_gateway)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> dict[str, Any]:
    try:
        return await gateway.fetch_status_list(
            auth,
            cursor=cursor,
            limit=limit,
            request_id=get_request_id(),
        )
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except (ScopeDeniedError, TenantBoundaryError) as exc:
        raise _privacy_http(exc) from exc


@router.post("/verify")
async def verify_credential_assist(
    body: VerifyCredentialRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    gateway: Annotated[JwtCredentialGateway, Depends(get_credential_gateway)],
) -> dict[str, Any]:
    try:
        result = await gateway.verify_assist(
            auth,
            body.credential,
            body.status_list,
            option_b_staleness=body.option_b_staleness,
            option_b_reason=body.option_b_reason,
        )
        return {
            "accepted": result.accepted,
            "reason": result.reason,
            "warnings": list(result.warnings),
        }
    except ScopeDeniedError as exc:
        raise _privacy_http(exc) from exc
