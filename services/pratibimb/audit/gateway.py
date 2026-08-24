"""Gateway-level audit helpers."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.audit.decorator import (
    _caller_kind_from_scopes,
    _emit_audit,
    _scope_str,
    _subject_from_params,
    build_audit_event,
)
from services.pratibimb.audit.sink import AuditSink, hash_params
from services.pratibimb.ledger_read.errors import (
    InsufficientCohortError,
    ScopeDeniedError,
)
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.ledger_read import ConsentScope


def emit_scope_denial_audit(
    service: LedgerReadService,
    *,
    query_kind: str,
    auth: AuthContext,
    params: Any,
    request_id: Optional[str] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> None:
    """Record a consent-scope denial that never reached the service layer."""
    sink: AuditSink = service._audit_sink
    now_fn = now or getattr(service, "_now", lambda: datetime.now(timezone.utc))
    params_hash, params_bytes = hash_params(params)
    event = build_audit_event(
        query_id=str(uuid.uuid4()),
        at_utc=now_fn(),
        tenant_id=auth.tenant_id,
        subject_pseudo_id=_subject_from_params(params),
        caller_kind="service",
        scope="*",
        query_kind=query_kind,
        params_hash=params_hash,
        params_bytes=params_bytes,
        outcome="scope_denied",
        duration_ms=0,
        error_kind="ScopeDeniedError",
        request_id=request_id,
    )
    _emit_audit(sink, event, fail_closed=True)


async def with_read_audit(
    service: LedgerReadService,
    *,
    query_kind: str,
    auth: AuthContext,
    params: Any,
    execute: Callable[[], Awaitable[Any]],
    granted_scopes: frozenset[ConsentScope],
    request_id: Optional[str] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> Any:
    """Run an async gateway operation and emit exactly one audit row."""
    sink: AuditSink = service._audit_sink
    now_fn = now or getattr(service, "_now", lambda: datetime.now(timezone.utc))

    params_hash, params_bytes = hash_params(params)
    subject = _subject_from_params(params)
    scope = _scope_str(granted_scopes)
    caller_kind = _caller_kind_from_scopes(granted_scopes)

    query_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    outcome = "error"
    error_kind: Optional[str] = None
    row_count: Optional[int] = None
    k_floor: Optional[int] = None

    try:
        response = await execute()
        outcome = "ok"
        from services.pratibimb.audit.decorator import _row_count

        row_count = _row_count(response)
        return response
    except ScopeDeniedError:
        outcome = "scope_denied"
        error_kind = "ScopeDeniedError"
        raise
    except InsufficientCohortError as exc:
        outcome = "insufficient_cohort"
        error_kind = "InsufficientCohortError"
        k_floor = getattr(exc, "min_k", exc.minimum_required)
        raise
    except Exception as exc:
        outcome = "error"
        error_kind = type(exc).__name__
        raise
    finally:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        event = build_audit_event(
            query_id=query_id,
            at_utc=now_fn(),
            tenant_id=auth.tenant_id,
            subject_pseudo_id=subject,
            caller_kind=caller_kind,
            scope=scope,
            query_kind=query_kind,
            params_hash=params_hash,
            params_bytes=params_bytes,
            outcome=outcome,
            duration_ms=duration_ms,
            result_row_count=row_count,
            k_anonymity_floor_applied=k_floor,
            error_kind=error_kind,
            request_id=request_id,
        )
        _emit_audit(sink, event, fail_closed=(outcome != "ok"))
