"""
AuditingLedgerReadService — wraps LedgerReadService with append-only audit emission.

One audit row per public method call. Audit uses its own transaction (SqlAuditSink).
Sink failures are logged and swallowed so they never mask the primary read outcome.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

from services.pratibimb.audit.metrics import (
    AUDIT_SINK_EMIT_TOTAL,
    AUDIT_SINK_FAILURE_TOTAL,
)
from services.pratibimb.audit.sink import AuditEvent, AuditSink, hash_params
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.errors import InsufficientCohortError, ScopeDeniedError
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink
from services.pratibimb.ledger_read.fingerprint import compute_result_fingerprint
from services.pratibimb.ledger_read.query_kinds import KNOWN_QUERY_KINDS, assert_known
from services.pratibimb.ledger_read.summative_guard import record_primary_sink_failure
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.ledger_read import (
    AggregatePatternReport,
    ConsentScope,
    CohortSummaryReport,
    EvidenceEventView,
    LearnerEvidenceFilter,
    QueryFilters,
    SessionSummaryView,
    SkillDecayCurve,
    UnitSafetyReport,
)

log = logging.getLogger(__name__)

_AGGREGATE_SUBJECT = "*"

_ERROR_KIND_MAP: dict[type[BaseException], str] = {
    InsufficientCohortError: "k_anonymity_floor",
    ScopeDeniedError: "consent_scope",
    PermissionError: "consent_scope",
}


def _classify_error(exc: BaseException) -> str:
    for cls, kind in _ERROR_KIND_MAP.items():
        if isinstance(exc, cls):
            return kind
    return "unexpected"


def _outcome_for(exc: BaseException | None) -> tuple[str, str | None, int | None]:
    if exc is None:
        return "ok", None, None
    if isinstance(exc, ScopeDeniedError) or isinstance(exc, PermissionError):
        return "scope_denied", _classify_error(exc), None
    if isinstance(exc, InsufficientCohortError):
        return "insufficient_cohort", _classify_error(exc), exc.minimum_required
    return "error", _classify_error(exc), None


def _caller_kind(scopes: frozenset[ConsentScope] | None) -> str:
    if not scopes:
        return "service"
    values = {s.value for s in scopes}
    if values & {ConsentScope.PRECEPTOR_REVIEW.value, ConsentScope.SELF_LEARNER.value}:
        return "preceptor"
    if values & {
        ConsentScope.AGGREGATE_ANALYTICS.value,
        ConsentScope.RESEARCH_DEIDENTIFIED.value,
    }:
        return "analyst"
    return "service"


def _scope_str(scopes: frozenset[ConsentScope] | None) -> str:
    if not scopes:
        return "*"
    return ",".join(sorted(s.value for s in scopes))


def _subject_from_params(params: dict[str, Any]) -> str:
    for key in ("learner_pseudo_id", "unit_id", "cohort_id"):
        value = params.get(key)
        if value:
            return str(value)
    filt = params.get("filt") or params.get("filters")
    if filt is not None:
        for key in ("learner_pseudo_id", "unit_id", "cohort_id"):
            value = getattr(filt, key, None)
            if value:
                return str(value)
    return _AGGREGATE_SUBJECT


def safe_emit(
    sink: AuditSink,
    event: AuditEvent,
    *,
    fallback: JsonlFallbackSink | None = None,
) -> None:
    """Primary emit with fallback capture; never masks read outcome."""
    tenant = event.tenant_id
    try:
        sink.emit(event)
        AUDIT_SINK_EMIT_TOTAL.labels(
            sink="primary", outcome="ok", tenant_id=tenant
        ).inc()
    except Exception as exc:
        AUDIT_SINK_FAILURE_TOTAL.labels(sink="primary", tenant_id=tenant).inc()
        record_primary_sink_failure(tenant)
        log.error(
            "audit_sink_emit_failed query_id=%s query_kind=%s tenant_id=%s sink_error=%r",
            event.query_id,
            event.query_kind,
            event.tenant_id,
            exc,
        )
        if fallback is not None:
            fallback.emit(event, fallback_reason=type(exc).__name__)


class AuditingLedgerReadService:
    """Wraps LedgerReadService — emits one audit row per call via _safe_emit."""

    _REGISTERED_KINDS: frozenset[str] = frozenset({
        "learner_evidence",
        "unit_safety_report",
        "aggregate_patterns",
        "cohort_summary",
        "list_cohort_sessions",
    })

    def __init__(
        self,
        inner: LedgerReadService,
        sink: AuditSink,
        *,
        fallback_sink: JsonlFallbackSink | None = None,
        request_id_provider: Callable[[], str | None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        registered = getattr(type(self), "_REGISTERED_KINDS", frozenset())
        unknown = registered - KNOWN_QUERY_KINDS
        if unknown:
            raise RuntimeError(
                f"AuditingLedgerReadService references unregistered "
                f"query_kinds: {sorted(unknown)}"
            )
        for kind in registered:
            assert_known(kind)
        self._inner = inner
        self._sink = sink
        self._fallback = fallback_sink
        self._request_id_provider = request_id_provider or (lambda: None)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _safe_emit(self, event: AuditEvent) -> None:
        safe_emit(self._sink, event, fallback=self._fallback)

    @property
    def fallback_sink(self) -> JsonlFallbackSink | None:
        return self._fallback

    @property
    def inner(self) -> LedgerReadService:
        return self._inner

    @property
    def sink(self) -> AuditSink:
        return self._sink

    @asynccontextmanager
    async def _audited(
        self,
        *,
        query_kind: str,
        params: dict[str, Any],
        auth: AuthContext,
        scopes: frozenset[ConsentScope] | None,
        request_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        assert_known(query_kind)
        query_id = str(uuid.uuid4())
        params_hash, params_bytes = hash_params(params)
        subject = _subject_from_params(params)
        t0 = time.monotonic()
        state: dict[str, Any] = {
            "outcome": "error",
            "result_row_count": None,
            "k_floor_applied": None,
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
            outcome, error_kind, k_floor = _outcome_for(exc)
            state["outcome"] = outcome
            state["error_kind"] = error_kind
            state["k_floor_applied"] = k_floor
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
                subject_pseudo_id=subject,
                caller_kind=_caller_kind(scopes),
                scope=_scope_str(scopes),
                query_kind=query_kind,
                query_params_hash=params_hash,
                query_params_bytes=params_bytes,
                outcome=state["outcome"],
                result_row_count=(
                    state["result_row_count"] if state["outcome"] == "ok" else None
                ),
                k_anonymity_floor_applied=state.get("k_floor_applied"),
                error_kind=state.get("error_kind"),
                request_id=request_id or self._request_id_provider(),
                duration_ms=duration_ms,
                result_fingerprint=state.get("result_fingerprint"),
            )
            self._safe_emit(event)

    def record_scope_denial(
        self,
        auth: AuthContext,
        query_kind: str,
        params: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        """Record consent denial that never reached the inner service."""
        assert_known(query_kind)
        params_hash, params_bytes = hash_params(params)
        event = AuditEvent(
            query_id=str(uuid.uuid4()),
            at_utc=self._now(),
            tenant_id=auth.tenant_id,
            subject_pseudo_id=_subject_from_params(
                params if isinstance(params, dict) else {"filt": params}
            ),
            caller_kind="service",
            scope="*",
            query_kind=query_kind,
            query_params_hash=params_hash,
            query_params_bytes=params_bytes,
            outcome="scope_denied",
            duration_ms=0,
            error_kind="consent_scope",
            request_id=request_id or self._request_id_provider(),
        )
        self._safe_emit(event)

    async def get_learner_evidence(
        self,
        filt: LearnerEvidenceFilter,
        scope: ConsentScope,
        *,
        auth: AuthContext,
        request_id: str | None = None,
    ) -> list[EvidenceEventView]:
        params = {"filt": filt, "scope": scope.value}
        scopes = frozenset({scope})
        async with self._audited(
            query_kind="learner_evidence",
            params=params,
            auth=auth,
            scopes=scopes,
            request_id=request_id,
        ) as state:
            result = await self._inner.get_learner_evidence(filt, scope)
            state["result_row_count"] = len(result)
            state["result"] = result
            return result

    async def get_unit_safety_report(
        self,
        tenant_id: str,
        unit_id: str,
        window_days: int,
        scope: ConsentScope,
        *,
        auth: AuthContext,
        dp_noise_epsilon: float | None = None,
        request_id: str | None = None,
    ) -> UnitSafetyReport:
        params = {
            "tenant_id": tenant_id,
            "unit_id": unit_id,
            "window_days": window_days,
            "scope": scope.value,
            "dp_noise_epsilon": dp_noise_epsilon,
        }
        scopes = frozenset({scope})
        async with self._audited(
            query_kind="unit_safety_report",
            params=params,
            auth=auth,
            scopes=scopes,
            request_id=request_id,
        ) as state:
            result = await self._inner.get_unit_safety_report(
                tenant_id, unit_id, window_days, scope, dp_noise_epsilon
            )
            state["result_row_count"] = result.cohort_size
            state["k_floor_applied"] = LedgerReadService.K_ANON_FLOOR
            state["result"] = result
            return result

    async def get_aggregate_error_patterns(
        self,
        filters: QueryFilters,
        scope: ConsentScope,
        *,
        auth: AuthContext,
        dp_noise_epsilon: float | None = None,
        request_id: str | None = None,
    ) -> AggregatePatternReport:
        params = {"filters": filters, "scope": scope.value}
        scopes = frozenset({scope})
        async with self._audited(
            query_kind="aggregate_patterns",
            params=params,
            auth=auth,
            scopes=scopes,
            request_id=request_id,
        ) as state:
            result = await self._inner.get_aggregate_error_patterns(
                filters, scope, dp_noise_epsilon
            )
            state["result_row_count"] = len(result.patterns)
            state["k_floor_applied"] = LedgerReadService.K_ANON_FLOOR
            state["result"] = result
            return result

    async def get_cohort_summary(
        self,
        cohort_id: str,
        *,
        scope: ConsentScope,
        auth: AuthContext,
        cause_prefix: str | None = None,
        request_id: str | None = None,
    ) -> CohortSummaryReport:
        params = {"cohort_id": cohort_id, "cause_prefix": cause_prefix}
        scopes = frozenset({scope})
        async with self._audited(
            query_kind="cohort_summary",
            params=params,
            auth=auth,
            scopes=scopes,
            request_id=request_id,
        ) as state:
            result = await self._inner.get_cohort_summary(
                cohort_id, scope=scope, cause_prefix=cause_prefix
            )
            state["result_row_count"] = result.cohort_size
            state["k_floor_applied"] = LedgerReadService.K_ANON_FLOOR
            state["result"] = result
            return result

    async def list_cohort_sessions(
        self,
        *,
        cohort_id: str,
        scope: ConsentScope,
        auth: AuthContext,
        case_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        request_id: str | None = None,
    ) -> list[SessionSummaryView]:
        params = {
            "cohort_id": cohort_id,
            "case_id": case_id,
            "limit": limit,
        }
        scopes = frozenset({scope})
        async with self._audited(
            query_kind="list_cohort_sessions",
            params=params,
            auth=auth,
            scopes=scopes,
            request_id=request_id,
        ) as state:
            result = await self._inner.list_cohort_sessions(
                cohort_id=cohort_id,
                scope=scope,
                case_id=case_id,
                since=since,
                until=until,
                limit=limit,
            )
            state["result_row_count"] = len(result)
            state["k_floor_applied"] = LedgerReadService.K_ANON_FLOOR
            state["result"] = result
            return result
