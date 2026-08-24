"""@audited — decorator for LedgerReadService methods (sync + async)."""
from __future__ import annotations

import functools
import inspect
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.sink import AuditEvent, AuditSink, hash_params
from services.pratibimb.ledger_read.errors import (
    InsufficientCohortError,
    ScopeDeniedError,
)
from shared.schemas.ledger_read import ConsentScope

log = logging.getLogger(__name__)


def _row_count(response: object) -> Optional[int]:
    if response is None:
        return 0
    fn = getattr(response, "__audit_row_count__", None)
    if callable(fn):
        try:
            return int(fn())
        except Exception:
            return None
    for attr in ("total", "cohort_size", "count"):
        value = getattr(response, attr, None)
        if isinstance(value, int):
            return value
    events = getattr(response, "events", None)
    if events is not None:
        try:
            return len(events)
        except TypeError:
            pass
    if hasattr(response, "__len__"):
        try:
            return len(response)  # type: ignore[arg-type]
        except TypeError:
            return None
    return None


def _caller_kind_from_scopes(scopes: frozenset[ConsentScope] | set[ConsentScope]) -> str:
    values = {getattr(s, "value", s) for s in scopes}
    if ConsentScope.PRECEPTOR_REVIEW.value in values:
        return "preceptor"
    if ConsentScope.SELF_LEARNER.value in values:
        return "preceptor"
    if ConsentScope.AGGREGATE_ANALYTICS.value in values:
        return "analyst"
    if ConsentScope.RESEARCH_DEIDENTIFIED.value in values:
        return "analyst"
    return "service"


def _scope_str(scopes: frozenset[ConsentScope] | set[ConsentScope]) -> str:
    return ",".join(sorted(getattr(s, "value", str(s)) for s in scopes))


def _subject_from_params(params: Any) -> str:
    for attr in ("learner_pseudo_id", "unit_id", "cohort_id"):
        value = getattr(params, attr, None)
        if value:
            return str(value)
    return "*"


def _extract_params(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if "filt" in kwargs:
        return kwargs["filt"]
    if "filters" in kwargs:
        return kwargs["filters"]
    if "cohort_id" in kwargs:
        return {
            "cohort_id": kwargs["cohort_id"],
            "case_id": kwargs.get("case_id"),
            "limit": kwargs.get("limit"),
        }
    if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
        # get_unit_safety_report(tenant_id, unit_id, window_days, ...)
        return {
            "tenant_id": args[0],
            "unit_id": args[1],
            "window_days": args[2] if len(args) > 2 else kwargs.get("window_days"),
        }
    if args:
        first = args[0]
        if hasattr(first, "model_dump") or isinstance(first, dict):
            return first
        if isinstance(first, str):
            return {"cohort_id": first}
    return {}


def build_audit_event(
    *,
    query_id: str,
    at_utc: datetime,
    tenant_id: str,
    subject_pseudo_id: str,
    caller_kind: str,
    scope: str,
    query_kind: str,
    params_hash: str,
    params_bytes: int,
    outcome: str,
    duration_ms: int,
    result_row_count: Optional[int] = None,
    k_anonymity_floor_applied: Optional[int] = None,
    error_kind: Optional[str] = None,
    request_id: Optional[str] = None,
) -> AuditEvent:
    return AuditEvent(
        query_id=query_id,
        at_utc=at_utc,
        tenant_id=tenant_id,
        subject_pseudo_id=subject_pseudo_id,
        caller_kind=caller_kind,
        scope=scope,
        query_kind=query_kind,
        query_params_hash=params_hash,
        query_params_bytes=params_bytes,
        outcome=outcome,
        duration_ms=duration_ms,
        result_row_count=result_row_count if outcome == "ok" else None,
        k_anonymity_floor_applied=k_anonymity_floor_applied,
        error_kind=error_kind,
        request_id=request_id,
    )


def _emit_audit(sink: AuditSink, event: AuditEvent, *, fail_closed: bool) -> None:
    try:
        sink.emit(event)
    except Exception as exc:
        if fail_closed:
            log.error(
                "ledger_read_audit.emit_failed_fail_closed query_id=%s outcome=%s",
                event.query_id,
                event.outcome,
            )
            raise AuditWriteError("audit sink unavailable on denial path") from exc
        log.exception(
            "ledger_read_audit.emit_failed_best_effort query_id=%s outcome=%s",
            event.query_id,
            event.outcome,
        )


async def _run_audited_async(
    self,
    fn: Callable,
    query_kind: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    sink: AuditSink = self._audit_sink
    now_fn = getattr(self, "_now", lambda: datetime.now(timezone.utc))

    audit_enabled = kwargs.pop("_audit", True)
    caller_id = kwargs.pop("caller_id", None)
    tenant_id = kwargs.pop("tenant_id", None)
    request_id = kwargs.pop("request_id", None)
    granted_scopes = kwargs.pop("granted_scopes", None)

    params = _extract_params(args, kwargs)
    params_hash, params_bytes = hash_params(params)
    subject = _subject_from_params(params)
    if isinstance(params, str):
        subject = params

    scope_set = frozenset(granted_scopes or ())
    scope = _scope_str(scope_set) if scope_set else "*"
    caller_kind = _caller_kind_from_scopes(scope_set) if scope_set else "service"

    query_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    outcome = "error"
    error_kind: Optional[str] = None
    row_count: Optional[int] = None
    k_floor: Optional[int] = None
    response = None

    try:
        response = await fn(self, *args, **kwargs)
        outcome = "ok"
        row_count = _row_count(response)
        return response
    except ScopeDeniedError:
        outcome = "scope_denied"
        error_kind = "ScopeDeniedError"
        raise
    except PermissionError as exc:
        outcome = "scope_denied"
        error_kind = type(exc).__name__
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
        if audit_enabled and caller_id is not None and tenant_id is not None:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            event = build_audit_event(
                query_id=query_id,
                at_utc=now_fn(),
                tenant_id=tenant_id,
                subject_pseudo_id=str(subject),
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


def _run_audited_sync(
    self,
    fn: Callable,
    query_kind: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    sink: AuditSink = self._audit_sink
    now_fn = getattr(self, "_now", lambda: datetime.now(timezone.utc))

    audit_enabled = kwargs.pop("_audit", True)
    caller_id = kwargs.pop("caller_id", None)
    tenant_id = kwargs.pop("tenant_id", None)
    request_id = kwargs.pop("request_id", None)
    granted_scopes = kwargs.pop("granted_scopes", None)

    params = _extract_params(args, kwargs)
    params_hash, params_bytes = hash_params(params)
    subject = _subject_from_params(params)
    if isinstance(params, str):
        subject = params

    scope_set = frozenset(granted_scopes or ())
    scope = _scope_str(scope_set) if scope_set else "*"
    caller_kind = _caller_kind_from_scopes(scope_set) if scope_set else "service"

    query_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    outcome = "error"
    error_kind: Optional[str] = None
    row_count: Optional[int] = None
    k_floor: Optional[int] = None
    response = None

    try:
        response = fn(self, *args, **kwargs)
        outcome = "ok"
        row_count = _row_count(response)
        return response
    except ScopeDeniedError:
        outcome = "scope_denied"
        error_kind = "ScopeDeniedError"
        raise
    except PermissionError as exc:
        outcome = "scope_denied"
        error_kind = type(exc).__name__
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
        if audit_enabled and caller_id is not None and tenant_id is not None:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            event = build_audit_event(
                query_id=query_id,
                at_utc=now_fn(),
                tenant_id=tenant_id,
                subject_pseudo_id=str(subject),
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


def audited(query_kind: str) -> Callable:
    """Decorate a LedgerReadService method — emits one AuditEvent per call."""

    def deco(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(self, *args, **kwargs):
                return await _run_audited_async(self, fn, query_kind, args, kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(self, *args, **kwargs):
            return _run_audited_sync(self, fn, query_kind, args, kwargs)

        return sync_wrapper

    return deco


# Alias used in unit tests and docs.
audited_read = audited
