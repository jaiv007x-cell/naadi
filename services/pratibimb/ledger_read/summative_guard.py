"""Summative-restricted mode — fail-closed for regulator-grade ledger reads."""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from typing import Callable, Final

from fastapi import Request
from starlette.responses import JSONResponse, Response

from services.pratibimb.audit.metrics import (
    AUDIT_PRIMARY_FAILURES_PER_MINUTE,
    AUDIT_SINK_FAILURE_TOTAL,
)
from services.pratibimb.auth.startup import is_production_auth_mode
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink

log = logging.getLogger(__name__)

# Regulator-grade caller labels (distinct from audit row caller_kind preceptor/analyst/service).
SUMMATIVE_CALLER_KINDS: frozenset[str] = frozenset({
    "regulator",
    "insurer_aggregate",
    "ncvet_audit",
})

# Closed vocabulary for 503 response bodies — regulators dashboard on this field.
RESTRICTION_REASONS: Final[frozenset[str]] = frozenset({
    "fallback_sink_unhealthy",
    "primary_sink_failure_rate_exceeded",
})

CALLER_KIND_HEADER = "x-ledger-caller-kind"
LEDGER_PATH_PREFIX = "/v1/ledger"
DEFAULT_RETRY_AFTER_SECONDS = 60

# Policy §3 critical thresholds (see docs/policy/audit_sink_failure_policy.md).
CRITICAL_FAILURES_PER_MINUTE = 50
FALLBACK_UNHEALTHY_SECONDS = 60

_failure_times: dict[str, deque[float]] = defaultdict(
    lambda: deque(maxlen=500)
)


class SummativeStartupError(RuntimeError):
    """Refusing to boot with summative restriction disabled in production."""


def assert_valid_restriction_reason(reason: str) -> None:
    if reason not in RESTRICTION_REASONS:
        raise ValueError(
            f"summative restriction reason {reason!r} not in {sorted(RESTRICTION_REASONS)}"
        )


def record_primary_sink_failure(tenant_id: str) -> None:
    """Track primary sink failures for per-tenant rate limiting and gauges."""
    _failure_times[tenant_id].append(time.monotonic())
    rate = primary_failures_per_minute(tenant_id)
    AUDIT_PRIMARY_FAILURES_PER_MINUTE.labels(tenant_id=tenant_id).set(rate)


def primary_failures_per_minute(tenant_id: str, *, window_seconds: int = 60) -> int:
    cutoff = time.monotonic() - window_seconds
    times = _failure_times[tenant_id]
    while times and times[0] < cutoff:
        times.popleft()
    return len(times)


def validate_summative_startup() -> None:
    """
    Boot guard for SUMMATIVE_RESTRICTION_ENABLED.

    Production refuses to start with the guard disabled — summative caller kinds
    are always accepted in the closed vocabulary, so disabling the guard would
    let regulator-grade reads through during degraded audit windows.
    """
    if summative_guard_enabled():
        return
    if is_production_auth_mode():
        raise SummativeStartupError(
            "AUTH_MODE=production with SUMMATIVE_RESTRICTION_ENABLED=false is forbidden. "
            "Summative-restricted middleware must remain enabled in production."
        )
    log.warning(
        "SUMMATIVE_RESTRICTION_ENABLED=false — summative caller kinds "
        "(%s) will NOT be restricted during audit sink degradation. "
        "Never disable this outside local development.",
        ", ".join(sorted(SUMMATIVE_CALLER_KINDS)),
    )

class SummativeRestrictionGuard:
    """
    Enter summative-restricted mode when audit durability cannot be guaranteed.

    Formative reads continue; regulator-grade callers receive 503 + Retry-After.
    """

    def __init__(
        self,
        fallback_sink: JsonlFallbackSink,
        *,
        critical_failures_per_minute: int = CRITICAL_FAILURES_PER_MINUTE,
        fallback_unhealthy_seconds: int = FALLBACK_UNHEALTHY_SECONDS,
        retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS,
    ) -> None:
        self._fallback = fallback_sink
        self._critical_failures_per_minute = critical_failures_per_minute
        self._fallback_unhealthy_seconds = fallback_unhealthy_seconds
        self._retry_after_seconds = retry_after_seconds

    @property
    def fallback_sink(self) -> JsonlFallbackSink:
        return self._fallback

    def caller_kind_from_request(self, request: Request) -> str | None:
        raw = request.headers.get(CALLER_KIND_HEADER, "").strip().lower()
        return raw or None

    def is_summative_caller(self, request: Request) -> bool:
        kind = self.caller_kind_from_request(request)
        return kind in SUMMATIVE_CALLER_KINDS

    def is_restricted(self, *, tenant_id: str | None = None) -> bool:
        if not self._fallback.is_healthy(
            within_seconds=self._fallback_unhealthy_seconds
        ):
            return True
        if tenant_id and (
            primary_failures_per_minute(tenant_id)
            >= self._critical_failures_per_minute
        ):
            return True
        return False

    def restriction_reason(self, *, tenant_id: str | None = None) -> str:
        if not self._fallback.is_healthy(
            within_seconds=self._fallback_unhealthy_seconds
        ):
            return "fallback_sink_unhealthy"
        if tenant_id and (
            primary_failures_per_minute(tenant_id)
            >= self._critical_failures_per_minute
        ):
            return "primary_sink_failure_rate_exceeded"
        raise RuntimeError("restriction_reason called while not restricted")

    def refusal_response(self, *, reason: str) -> JSONResponse:
        assert_valid_restriction_reason(reason)
        log.warning(
            "summative_restricted reason=%s failures_total=%s",
            reason,
            AUDIT_SINK_FAILURE_TOTAL.total(),
        )
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(self._retry_after_seconds)},
            content={
                "detail": {
                    "error": "summative_restricted",
                    "reason": reason,
                    "message": (
                        "Audit sink durability cannot be guaranteed; "
                        "summative-grade reads are temporarily refused."
                    ),
                }
            },
        )


def summative_restriction_response(
    request: Request,
    guard: SummativeRestrictionGuard,
    *,
    tenant_id_provider: Callable[[Request], str | None] | None = None,
) -> Response | None:
    """
    Return a 503 response for summative callers when restricted; else None.

    Only applies to /v1/ledger/* paths. Formative callers are unaffected.
    """
    if not request.url.path.startswith(LEDGER_PATH_PREFIX):
        return None
    if not guard.is_summative_caller(request):
        return None

    tenant_id = None
    if tenant_id_provider is not None:
        try:
            tenant_id = tenant_id_provider(request)
        except Exception:
            tenant_id = None

    if not guard.is_restricted(tenant_id=tenant_id):
        return None

    return guard.refusal_response(reason=guard.restriction_reason(tenant_id=tenant_id))


def tenant_id_from_request_headers(request: Request) -> str | None:
    """Best-effort tenant for rate checks before route-level auth runs."""
    for header in ("x-dev-tenant", "x-tenant-id"):
        value = request.headers.get(header)
        if value:
            return value.strip()
    return None


def summative_guard_enabled() -> bool:
    raw = os.environ.get("SUMMATIVE_RESTRICTION_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no"}
