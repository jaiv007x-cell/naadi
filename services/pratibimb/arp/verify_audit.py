"""Shape A: unconditional audit emit on verifier→NAADI ARP callbacks (I.b)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import AUDIT_SINK_FAILURE_TOTAL
from services.pratibimb.audit.sink import AuditEvent, AuditSink, hash_params
from services.pratibimb.auth.context import AuthContext

_VERIFIER = "ncvet_arp_verifier"
_SURFACE = "arp"  # I.c cutover; pre-cutover samples under surface=regrade unchanged

VERIFY_ASSIST_KIND = "verify_arp_assist"
FETCH_ARTIFACT_KIND = "fetch_arp_artifact"


def emit_arp_verify_audit(
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

    Callers MUST invoke this **before** returning the callback response body.
    Params SHOULD include presented_digest + stored_digest on success and failure
    (I.b C3 / pin 5).
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
                "arp_verify_audit_unavailable",
                correlation_id=event.query_id,
            ) from exc
        raise
