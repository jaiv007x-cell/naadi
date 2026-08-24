"""E3: audited wrapper for authoring catalog ledger reads."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import (
    AUDIT_SINK_EMIT_TOTAL,
    AUDIT_SINK_FAILURE_TOTAL,
)
from services.pratibimb.audit.sink import AuditEvent, AuditSink, hash_params
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.schemas import PublishedCaseVersionView, RetiredCaseVersionView
from services.pratibimb.ledger_read.audit_decorator import safe_emit
from services.pratibimb.ledger_read.catalog_metrics import record_catalog_audit_metrics
from services.pratibimb.ledger_read.catalog_audit_params import catalog_audit_params
from services.pratibimb.ledger_read.summative_guard import record_primary_sink_failure
from services.pratibimb.ledger_read.catalog_service import (
    AuthoringCatalogReadService,
    CatalogPageResult,
    PublishedCaseVersionsFilter,
    RetirementHistoryFilter,
)
from services.pratibimb.ledger_read.errors import ScopeDeniedError, TenantBoundaryError
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink
from services.pratibimb.ledger_read.fingerprint import compute_result_fingerprint
from services.pratibimb.ledger_read.query_kinds import (
    E3_AUTHORING_CATALOG_KINDS,
    assert_e3_catalog_kind,
)
from shared.schemas.ledger_read import ConsentScope

log = logging.getLogger(__name__)

_CATALOG_SUBJECT = "*"
_FAIL_CLOSED_KINDS = frozenset({"get_retirement_history"})

_ERROR_KIND_MAP: dict[type[BaseException], str] = {
    ScopeDeniedError: "consent_scope",
    TenantBoundaryError: "tenant_mismatch",
    PermissionError: "consent_scope",
}


def _classify_error(exc: BaseException) -> str:
    for cls, kind in _ERROR_KIND_MAP.items():
        if isinstance(exc, cls):
            return kind
    return "unexpected"


def _outcome_for(exc: BaseException | None) -> tuple[str, str | None]:
    if exc is None:
        return "ok", None
    if isinstance(exc, (ScopeDeniedError, PermissionError)):
        return "scope_denied", _classify_error(exc)
    if isinstance(exc, TenantBoundaryError):
        return "scope_denied", _classify_error(exc)
    return "error", _classify_error(exc)


def catalog_safe_emit(
    sink: AuditSink,
    event: AuditEvent,
    *,
    query_kind: str,
    fallback: JsonlFallbackSink | None = None,
    payload_row_count: int = 0,
) -> None:
    """Per-kind sink policy — I-E3-4; metrics paired here (I-E3-10 option a)."""
    fail_closed = query_kind in _FAIL_CLOSED_KINDS
    tenant = event.tenant_id
    try:
        sink.emit(event)
        AUDIT_SINK_EMIT_TOTAL.labels(
            sink="primary", outcome="ok", tenant_id=tenant
        ).inc()
    except Exception as exc:
        if fail_closed:
            AUDIT_SINK_FAILURE_TOTAL.labels(sink="primary", surface="catalog", tenant_id=tenant).inc()
            record_primary_sink_failure(tenant)
            log.error(
                "catalog_audit.emit_failed_fail_closed query_id=%s kind=%s outcome=%s",
                event.query_id,
                query_kind,
                event.outcome,
            )
            raise AuditWriteError(
                "audit sink unavailable for catalog read",
                correlation_id=event.request_id,
                retry_after_seconds=30,
            ) from exc
        safe_emit(sink, event, fallback=fallback)
    record_catalog_audit_metrics(event, payload_row_count=payload_row_count)


class AuditingAuthoringCatalogReadService:
    """Wraps AuthoringCatalogReadService — one ledger_read_audit row per call."""

    _REGISTERED_KINDS: frozenset[str] = frozenset({
        "get_published_case_versions",
        "get_retirement_history",
    })

    def __init__(
        self,
        inner: AuthoringCatalogReadService,
        sink: AuditSink,
        *,
        fallback_sink: JsonlFallbackSink | None = None,
        request_id_provider: Callable[[], str | None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        registered = getattr(type(self), "_REGISTERED_KINDS", frozenset())
        unknown = registered - E3_AUTHORING_CATALOG_KINDS
        if unknown:
            raise RuntimeError(
                f"AuditingAuthoringCatalogReadService references kinds outside "
                f"E3_AUTHORING_CATALOG_KINDS: {sorted(unknown)}"
            )
        for kind in registered:
            assert_e3_catalog_kind(kind)
        self._inner = inner
        self._sink = sink
        self._fallback = fallback_sink
        self._request_id_provider = request_id_provider or (lambda: None)
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def inner(self) -> AuthoringCatalogReadService:
        return self._inner

    @property
    def sink(self) -> AuditSink:
        return self._sink

    def _catalog_payload_row_count(self, result: Any) -> int:
        """Rows in response payload after auth/redaction — I-E3-13."""
        if result is None:
            return 0
        return len(result)

    def _emit(
        self,
        event: AuditEvent,
        *,
        query_kind: str,
        payload_row_count: int = 0,
    ) -> None:
        catalog_safe_emit(
            self._sink,
            event,
            query_kind=query_kind,
            fallback=self._fallback,
            payload_row_count=payload_row_count,
        )

    @asynccontextmanager
    async def _audited(
        self,
        *,
        query_kind: str,
        params: dict[str, Any],
        auth: AuthContext,
        scope: ConsentScope,
        request_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Wrap one catalog read with in-band audit emission (I-E3-4).

        Emit runs in this ``finally`` block **before** the handler returns
        serialized rows to the caller. A fail-closed sink rejection therefore
        aborts the return path — audit is never moved off the response path
        (no background task), or fail-closed silently becomes fail-open.
        """
        assert_e3_catalog_kind(query_kind)
        query_id = str(uuid.uuid4())
        params_hash, params_bytes = hash_params(params)
        t0 = time.monotonic()
        state: dict[str, Any] = {
            "outcome": "error",
            "result_row_count": None,
            "error_kind": None,
            "result": None,
            "result_fingerprint": None,
        }
        caught: BaseException | None = None

        try:
            yield state
            state["outcome"] = "ok"
        except BaseException as exc:
            caught = exc
            outcome, error_kind = _outcome_for(exc)
            state["outcome"] = outcome
            state["error_kind"] = error_kind
            raise
        finally:
            duration_ms = int((time.monotonic() - t0) * 1000)
            if state["outcome"] == "ok":
                if state["result"] is None:
                    raise RuntimeError(
                        f"_audited({query_kind!r}) exited success without "
                        "state['result'] set; cannot compute fingerprint"
                    )
                state["result_fingerprint"] = compute_result_fingerprint(
                    state["result"]
                )
            event = AuditEvent(
                query_id=query_id,
                at_utc=self._now(),
                tenant_id=auth.tenant_id,
                subject_pseudo_id=_CATALOG_SUBJECT,
                actor_subject_id=auth.subject_pseudo_id,
                caller_kind="authoring",
                scope=scope.value,
                query_kind=query_kind,
                query_params_hash=params_hash,
                query_params_bytes=params_bytes,
                outcome=state["outcome"],
                result_row_count=(
                    state["result_row_count"] if state["outcome"] == "ok" else None
                ),
                error_kind=state.get("error_kind"),
                request_id=request_id or self._request_id_provider(),
                duration_ms=duration_ms,
                result_fingerprint=state.get("result_fingerprint"),
            )
            payload_row_count = (
                self._catalog_payload_row_count(state["result"])
                if state["outcome"] == "ok"
                else 0
            )  # post-redaction; see I-E3-13
            self._emit(
                event,
                query_kind=query_kind,
                payload_row_count=payload_row_count,
            )

    def record_scope_denial(
        self,
        auth: AuthContext,
        query_kind: str,
        params: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        assert_e3_catalog_kind(query_kind)
        params_hash, params_bytes = hash_params(params)
        event = AuditEvent(
            query_id=str(uuid.uuid4()),
            at_utc=self._now(),
            tenant_id=auth.tenant_id,
            subject_pseudo_id=_CATALOG_SUBJECT,
            actor_subject_id=auth.subject_pseudo_id,
            caller_kind="authoring",
            scope="*",
            query_kind=query_kind,
            query_params_hash=params_hash,
            query_params_bytes=params_bytes,
            outcome="scope_denied",
            duration_ms=0,
            error_kind="consent_scope",
            request_id=request_id or self._request_id_provider(),
        )
        self._emit(event, query_kind=query_kind)

    def record_tenant_denial(
        self,
        auth: AuthContext,
        query_kind: str,
        params: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        assert_e3_catalog_kind(query_kind)
        params_hash, params_bytes = hash_params(params)
        event = AuditEvent(
            query_id=str(uuid.uuid4()),
            at_utc=self._now(),
            tenant_id=auth.tenant_id,
            subject_pseudo_id=_CATALOG_SUBJECT,
            actor_subject_id=auth.subject_pseudo_id,
            caller_kind="authoring",
            scope="*",
            query_kind=query_kind,
            query_params_hash=params_hash,
            query_params_bytes=params_bytes,
            outcome="scope_denied",
            duration_ms=0,
            error_kind="tenant_mismatch",
            request_id=request_id or self._request_id_provider(),
        )
        self._emit(event, query_kind=query_kind)

    def record_cursor_invalid(
        self,
        auth: AuthContext,
        query_kind: str,
        params: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        assert_e3_catalog_kind(query_kind)
        params_hash, params_bytes = hash_params(params)
        event = AuditEvent(
            query_id=str(uuid.uuid4()),
            at_utc=self._now(),
            tenant_id=auth.tenant_id,
            subject_pseudo_id=_CATALOG_SUBJECT,
            actor_subject_id=auth.subject_pseudo_id,
            caller_kind="authoring",
            scope="*",
            query_kind=query_kind,
            query_params_hash=params_hash,
            query_params_bytes=params_bytes,
            outcome="error",
            duration_ms=0,
            error_kind="cursor_invalid",
            request_id=request_id or self._request_id_provider(),
        )
        self._emit(event, query_kind=query_kind)

    async def get_published_case_versions(
        self,
        filt: PublishedCaseVersionsFilter,
        *,
        scope: ConsentScope,
        auth: AuthContext,
        request_id: str | None = None,
    ) -> CatalogPageResult[PublishedCaseVersionView]:
        params = catalog_audit_params(
            tenant_id=filt.tenant_id,
            case_id=filt.case_id,
            include_retired=filt.include_retired,
            limit=filt.limit,
            cursor=filt.cursor,
        )
        async with self._audited(
            query_kind="get_published_case_versions",
            params=params,
            auth=auth,
            scope=scope,
            request_id=request_id,
        ) as state:
            result = self._inner.get_published_case_versions(filt, auth=auth)
            state["result_row_count"] = len(result.items)
            state["result"] = result.items
            return result

    async def get_published_case_version(
        self,
        *,
        tenant_id: str,
        case_id: str,
        version: str,
        scope: ConsentScope,
        auth: AuthContext,
        request_id: str | None = None,
    ) -> PublishedCaseVersionView | None:
        params = {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "version": version,
        }
        async with self._audited(
            query_kind="get_published_case_versions",
            params=params,
            auth=auth,
            scope=scope,
            request_id=request_id,
        ) as state:
            result = self._inner.get_published_case_version(
                tenant_id=tenant_id,
                case_id=case_id,
                version=version,
                auth=auth,
            )
            state["result_row_count"] = 0 if result is None else 1
            state["result"] = [result] if result is not None else []
            return result

    async def get_retirement_history(
        self,
        filt: RetirementHistoryFilter,
        *,
        scope: ConsentScope,
        auth: AuthContext,
        request_id: str | None = None,
    ) -> CatalogPageResult[RetiredCaseVersionView]:
        params = catalog_audit_params(
            tenant_id=filt.tenant_id,
            case_id=filt.case_id,
            limit=filt.limit,
            cursor=filt.cursor,
        )
        async with self._audited(
            query_kind="get_retirement_history",
            params=params,
            auth=auth,
            scope=scope,
            request_id=request_id,
        ) as state:
            result = self._inner.get_retirement_history(filt, auth=auth)
            state["result_row_count"] = len(result.items)
            state["result"] = result.items
            return result
