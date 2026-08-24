"""F.a/F.b: audited wrapper for NCVET reads."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import (
    AUDIT_SINK_EMIT_TOTAL,
    AUDIT_SINK_FAILURE_TOTAL,
)
from services.pratibimb.audit.sink import AuditEvent, AuditSink, hash_params
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.audit_decorator import safe_emit
from services.pratibimb.ledger_read.errors import (
    InvalidNcvetCursorError,
    ScopeDeniedError,
    TenantBoundaryError,
)
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink
from services.pratibimb.ledger_read.ncvet_metrics import record_ncvet_audit_metrics
from services.pratibimb.ledger_read.ncvet_service import (
    LearnerSessionsFilter,
    NcvetEvidenceReadService,
    NcvetListPageResult,
    SessionEvidenceNotFoundError,
    resolve_list_page_ordinal,
)
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS, assert_f_ncvet_kind
from services.pratibimb.ledger_read.summative_guard import record_primary_sink_failure
from shared.schemas.ledger_read import ConsentScope, NcvetSessionEvidenceView

log = logging.getLogger(__name__)

_NCVET_SUBJECT = "*"
_FAIL_CLOSED_KINDS = frozenset({"get_session_evidence", "list_learner_sessions"})
_NCVET_CALLER_KIND = "ncvet_audit"

_ERROR_KIND_MAP: dict[type[BaseException], str] = {
    ScopeDeniedError: "consent_scope",
    TenantBoundaryError: "tenant_mismatch",
    PermissionError: "consent_scope",
    SessionEvidenceNotFoundError: "not_found",
    InvalidNcvetCursorError: "cursor_invalid",
}


def ncvet_list_audit_params(
    *,
    tenant_id: str,
    learner_pseudo_id: str,
    limit: int,
    cursor: str | None,
    page_ordinal: int,
) -> dict[str, Any]:
    """
    Hashed audit params for ``list_learner_sessions`` (I-F-7).

    ``page_ordinal`` is server-derived from cursor decode — never accepted from
    client input. See ``resolve_list_page_ordinal`` in ``ncvet_service.py``.
    """
    return {
        "tenant_id": tenant_id,
        "learner_pseudo_id": learner_pseudo_id,
        "limit": limit,
        "cursor": cursor,
        "page_ordinal": page_ordinal,
    }


def _classify_error(exc: BaseException) -> str:
    for cls, kind in _ERROR_KIND_MAP.items():
        if isinstance(exc, cls):
            return kind
    return "unexpected"


def _outcome_for(exc: BaseException | None) -> tuple[str, str | None]:
    if exc is None:
        return "ok", None
    if isinstance(exc, SessionEvidenceNotFoundError):
        return "error", "not_found"
    if isinstance(exc, InvalidNcvetCursorError):
        return "error", "cursor_invalid"
    if isinstance(exc, (ScopeDeniedError, PermissionError)):
        return "scope_denied", _classify_error(exc)
    if isinstance(exc, TenantBoundaryError):
        return "scope_denied", _classify_error(exc)
    return "error", _classify_error(exc)


def ncvet_safe_emit(
    sink: AuditSink,
    event: AuditEvent,
    *,
    query_kind: str,
    fallback: JsonlFallbackSink | None = None,
    payload_row_count: int = 0,
) -> None:
    """I-F-6: fail-closed emit for NCVET kinds; metrics paired here (I-E3-10 option a)."""
    fail_closed = query_kind in _FAIL_CLOSED_KINDS
    tenant = event.tenant_id
    try:
        sink.emit(event)
        AUDIT_SINK_EMIT_TOTAL.labels(
            sink="primary", outcome="ok", tenant_id=tenant
        ).inc()
    except Exception as exc:
        if fail_closed:
            AUDIT_SINK_FAILURE_TOTAL.labels(sink="primary", surface="ncvet", tenant_id=tenant).inc()
            record_primary_sink_failure(tenant)
            log.error(
                "ncvet_audit.emit_failed_fail_closed query_id=%s kind=%s outcome=%s",
                event.query_id,
                query_kind,
                event.outcome,
            )
            raise AuditWriteError(
                "audit sink unavailable for ncvet read",
                correlation_id=event.request_id,
                retry_after_seconds=30,
            ) from exc
        safe_emit(sink, event, fallback=fallback)
    record_ncvet_audit_metrics(event, payload_row_count=payload_row_count)

class AuditingNcvetReadService:
    """Wraps NcvetEvidenceReadService — one ledger_read_audit row per call/page."""

    _REGISTERED_KINDS: frozenset[str] = frozenset({
        "get_session_evidence",
        "list_learner_sessions",
    })

    def __init__(
        self,
        inner: NcvetEvidenceReadService,
        sink: AuditSink,
        *,
        fallback_sink: JsonlFallbackSink | None = None,
        request_id_provider: Callable[[], str | None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        registered = getattr(type(self), "_REGISTERED_KINDS", frozenset())
        unknown = registered - F_NCVET_QUERY_KINDS
        if unknown:
            raise RuntimeError(
                f"AuditingNcvetReadService references kinds outside "
                f"F_NCVET_QUERY_KINDS: {sorted(unknown)}"
            )
        for kind in registered:
            assert_f_ncvet_kind(kind)
        self._inner = inner
        self._sink = sink
        self._fallback = fallback_sink
        self._request_id_provider = request_id_provider or (lambda: None)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._default_caller_kind = _NCVET_CALLER_KIND

    def with_caller_kind(self, caller_kind: str) -> "AuditingNcvetReadService":
        """Return a shallow clone that emits audit rows with ``caller_kind`` (G.a)."""
        from services.pratibimb.audit.caller_kinds import assert_known_caller_kind

        assert_known_caller_kind(caller_kind)
        clone = AuditingNcvetReadService(
            self._inner,
            self._sink,
            fallback_sink=self._fallback,
            request_id_provider=self._request_id_provider,
            now=self._now,
        )
        clone._default_caller_kind = caller_kind
        return clone

    def record_scope_denial(
        self,
        auth: AuthContext,
        query_kind: str,
        params: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        assert_f_ncvet_kind(query_kind)
        params_hash, params_bytes = hash_params(params)
        event = AuditEvent(
            query_id=str(uuid.uuid4()),
            at_utc=self._now(),
            tenant_id=auth.tenant_id,
            subject_pseudo_id=_NCVET_SUBJECT,
            actor_subject_id=auth.subject_pseudo_id,
            caller_kind=self._default_caller_kind,
            scope="*",
            query_kind=query_kind,
            query_params_hash=params_hash,
            query_params_bytes=params_bytes,
            outcome="scope_denied",
            duration_ms=0,
            error_kind="consent_scope",
            request_id=request_id or self._request_id_provider(),
        )
        ncvet_safe_emit(self._sink, event, query_kind=query_kind, fallback=self._fallback)

    def record_tenant_denial(
        self,
        auth: AuthContext,
        query_kind: str,
        params: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        assert_f_ncvet_kind(query_kind)
        params_hash, params_bytes = hash_params(params)
        event = AuditEvent(
            query_id=str(uuid.uuid4()),
            at_utc=self._now(),
            tenant_id=auth.tenant_id,
            subject_pseudo_id=_NCVET_SUBJECT,
            actor_subject_id=auth.subject_pseudo_id,
            caller_kind=self._default_caller_kind,
            scope="*",
            query_kind=query_kind,
            query_params_hash=params_hash,
            query_params_bytes=params_bytes,
            outcome="scope_denied",
            duration_ms=0,
            error_kind="tenant_mismatch",
            request_id=request_id or self._request_id_provider(),
        )
        ncvet_safe_emit(self._sink, event, query_kind=query_kind, fallback=self._fallback)

    def record_cursor_invalid(
        self,
        auth: AuthContext,
        query_kind: str,
        params: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        assert_f_ncvet_kind(query_kind)
        params_hash, params_bytes = hash_params(params)
        event = AuditEvent(
            query_id=str(uuid.uuid4()),
            at_utc=self._now(),
            tenant_id=auth.tenant_id,
            subject_pseudo_id=_NCVET_SUBJECT,
            actor_subject_id=auth.subject_pseudo_id,
            caller_kind=self._default_caller_kind,
            scope="*",
            query_kind=query_kind,
            query_params_hash=params_hash,
            query_params_bytes=params_bytes,
            outcome="error",
            duration_ms=0,
            error_kind="cursor_invalid",
            request_id=request_id or self._request_id_provider(),
        )
        ncvet_safe_emit(self._sink, event, query_kind=query_kind, fallback=self._fallback)

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
        from services.pratibimb.ledger_read.fingerprint import compute_result_fingerprint

        assert_f_ncvet_kind(query_kind)
        query_id = str(uuid.uuid4())
        params_hash, params_bytes = hash_params(params)
        t0 = time.monotonic()
        state: dict[str, Any] = {
            "outcome": "error",
            "result_row_count": None,
            "result": None,
            "query_id": query_id,
            "result_fingerprint": None,
        }
        try:
            yield state
            state["outcome"] = "ok"
        except BaseException as exc:
            outcome, error_kind = _outcome_for(exc)
            state["outcome"] = outcome
            state["error_kind"] = error_kind
            raise
        finally:
            duration_ms = int((time.monotonic() - t0) * 1000)
            if state["outcome"] == "ok" and state.get("result_row_count") is None:
                state["result_row_count"] = 1 if state["result"] is not None else 0
            if state["outcome"] == "ok" and state.get("result") is not None:
                state["result_fingerprint"] = compute_result_fingerprint(state["result"])
            event = AuditEvent(
                query_id=query_id,
                at_utc=self._now(),
                tenant_id=auth.tenant_id,
                subject_pseudo_id=_NCVET_SUBJECT,
                actor_subject_id=auth.subject_pseudo_id,
                caller_kind=self._default_caller_kind,
                scope=scope.value,
                query_kind=query_kind,
                query_params_hash=params_hash,
                query_params_bytes=params_bytes,
                outcome=state["outcome"],
                result_row_count=state.get("result_row_count"),
                result_fingerprint=state.get("result_fingerprint"),
                error_kind=state.get("error_kind"),
                request_id=request_id or self._request_id_provider(),
                duration_ms=duration_ms,
            )
            ncvet_safe_emit(
                self._sink,
                event,
                query_kind=query_kind,
                fallback=self._fallback,
                payload_row_count=state.get("result_row_count") or 0,
            )

    async def get_session_evidence(
        self,
        session_id: str,
        *,
        scope: ConsentScope,
        auth: AuthContext,
        tenant_id: str | None = None,
        request_id: str | None = None,
    ) -> NcvetSessionEvidenceView:
        view, _query_id, _fp = await self.get_session_evidence_with_audit_meta(
            session_id,
            scope=scope,
            auth=auth,
            tenant_id=tenant_id,
            request_id=request_id,
        )
        return view

    async def get_session_evidence_with_audit_meta(
        self,
        session_id: str,
        *,
        scope: ConsentScope,
        auth: AuthContext,
        tenant_id: str | None = None,
        request_id: str | None = None,
    ) -> tuple[NcvetSessionEvidenceView, str, str | None]:
        """Return view + (audit query_id, result_fingerprint). Used by G.a issuer."""
        params = {"session_id": session_id, "tenant_id": tenant_id or auth.tenant_id}
        async with self._audited(
            query_kind="get_session_evidence",
            params=params,
            auth=auth,
            scope=scope,
            request_id=request_id,
        ) as state:
            result = self._inner.get_session_evidence(
                session_id,
                auth=auth,
                tenant_id=tenant_id,
            )
            state["result"] = result
        return result, state["query_id"], state.get("result_fingerprint")

    async def list_learner_sessions(
        self,
        filt: LearnerSessionsFilter,
        *,
        scope: ConsentScope,
        auth: AuthContext,
        request_id: str | None = None,
    ) -> NcvetListPageResult:
        page_ordinal, _ = resolve_list_page_ordinal(
            filt.cursor,
            learner_pseudo_id=filt.learner_pseudo_id,
        )
        params = ncvet_list_audit_params(
            tenant_id=filt.tenant_id,
            learner_pseudo_id=filt.learner_pseudo_id,
            limit=filt.limit,
            cursor=filt.cursor,
            page_ordinal=page_ordinal,
        )
        async with self._audited(
            query_kind="list_learner_sessions",
            params=params,
            auth=auth,
            scope=scope,
            request_id=request_id,
        ) as state:
            result = self._inner.list_learner_sessions(filt, auth=auth)
            state["result"] = result
            state["result_row_count"] = len(result.items)
            return result
