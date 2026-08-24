"""SAMVAAD.c — POST /v1/samvaad/verify."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.pratibimb.app.api.deps import get_auth_context
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger.db import ledger_session
from services.pratibimb.ledger_read.deps import get_audit_sink
from services.pratibimb.ledger_read.request_context import get_request_id
from services.pratibimb.samvaad.dhaara_projection import (
    get_production_projection_sink,
)
from services.pratibimb.samvaad.summative_contract import SummativeEvidenceRejectedError
from services.pratibimb.samvaad.verify_service import verify_samvaad

router = APIRouter(prefix="/v1/samvaad", tags=["samvaad"])


class VerifySamvaadRequest(BaseModel):
    assessment_kind: str = Field(default="summative")
    dry_run: bool | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict)


def _audit_unavailable_http(exc: AuditWriteError) -> HTTPException:
    correlation_id = getattr(exc, "correlation_id", None) or get_request_id() or str(
        uuid.uuid4()
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "audit_unavailable", "correlation_id": correlation_id},
    )


@router.post("/verify")
async def verify_samvaad_route(
    req: VerifySamvaadRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict[str, Any]:
    body = req.model_dump()
    if req.dry_run is None:
        body.pop("dry_run", None)
    try:
        with ledger_session() as session:
            return verify_samvaad(
                body,
                tenant_id=auth.tenant_id,
                sink=get_audit_sink(),
                ledger_session=session,
                projection_sink=get_production_projection_sink(),
                learner_pseudo_id=auth.subject_pseudo_id,
                session_anchor=get_request_id() or str(uuid.uuid4()),
            )
    except AuditWriteError as exc:
        raise _audit_unavailable_http(exc) from exc
    except SummativeEvidenceRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_kind": exc.error_kind, "message": str(exc)},
        ) from exc
