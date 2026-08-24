"""Shape A: unconditional audit emit on verifier→NAADI regrade callbacks (H.c)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import AUDIT_SINK_FAILURE_TOTAL
from services.pratibimb.audit.sink import AuditEvent, AuditSink, hash_params
from services.pratibimb.auth.context import AuthContext

_VERIFIER = "ncvet_verifier"
_SURFACE = "regrade"

VERIFY_ASSIST_KIND = "verify_regrade_assist"
FETCH_ARTIFACT_KIND = "fetch_regrade_artifact"


def emit_regrade_verify_audit(
    sink: AuditSink,
    *,
    auth: AuthContext,
    scope: str,
    query_kind: str,
    params: dict[str, Any],
    outcome: str = "ok",
    error_kind: str | None = None,
    request_id: str | None = None,
    fail_closed: bool = False,
) -> None:
    """
    Shape A: always emit when a verifier callback hits NAADI.

    Callers (``JwtRegradeGateway.verify_assist`` / ``fetch_artifact``) MUST invoke this
    **before** returning the callback response body — emit-then-respond. That way a
    sink failure cannot yield HTTP 200 to the verifier while NAADI has no audit row.

    On sink failure: increment ``audit_sink_failure_total{surface="regrade"}`` and
    raise ``AuditWriteError`` when ``fail_closed`` (HTTP 503).
    """
    now = datetime.now(timezone.utc)
    ph, pb = hash_params(params)
    event = AuditEvent(
        query_id=str(uuid.uuid4()),
        at_utc=now,
        tenant_id=auth.tenant_id,
        subject_pseudo_id=auth.subject_pseudo_id,
        actor_subject_id=auth.subject_pseudo_id,
        caller_kind=_VERIFIER,
        scope=scope,
        query_kind=query_kind,
        query_params_hash=ph,
        query_params_bytes=pb,
        outcome=outcome,
        duration_ms=0,
        error_kind=error_kind,
        request_id=request_id,
    )
    try:
        sink.emit(event)
    except Exception as exc:
        AUDIT_SINK_FAILURE_TOTAL.labels(
            sink="primary",
            surface=_SURFACE,
            tenant_id=auth.tenant_id,
        ).inc()
        if fail_closed:
            raise AuditWriteError(
                "regrade_verify_audit_unavailable",
                correlation_id=event.query_id,
            ) from exc
        raise
