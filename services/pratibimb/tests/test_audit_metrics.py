"""Audit sink Prometheus metrics tests."""
from __future__ import annotations

from services.pratibimb.audit.metrics import render_prometheus_metrics
from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink
from services.pratibimb.ledger_read.audit_decorator import safe_emit
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink
from services.pratibimb.ledger_read.summative_guard import record_primary_sink_failure

from datetime import datetime, timezone


def _event(tenant_id: str = "tenant-a") -> AuditEvent:
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    return AuditEvent(
        query_id="q-1",
        at_utc=now,
        tenant_id=tenant_id,
        subject_pseudo_id="L1",
        caller_kind="preceptor",
        scope="preceptor.review",
        query_kind="learner_evidence",
        query_params_hash="abc",
        query_params_bytes=3,
        outcome="ok",
        duration_ms=1,
        result_row_count=1,
    )


def test_primary_success_increments_emit_counter():
    sink = InMemoryAuditSink()
    safe_emit(sink, _event(tenant_id="tenant-metrics-primary-ok"))
    body = render_prometheus_metrics()
    assert (
        'audit_sink_emit_total{outcome="ok",sink="primary",'
        'tenant_id="tenant-metrics-primary-ok"} 1'
        in body
    )


def test_primary_failure_increments_failure_and_fallback_emit(tmp_path):
    class _FailSink(InMemoryAuditSink):
        def emit(self, event: AuditEvent) -> None:
            raise RuntimeError("db down")

    fallback = JsonlFallbackSink(tmp_path)
    safe_emit(_FailSink(), _event(tenant_id="tenant-metrics-fallback"), fallback=fallback)
    body = render_prometheus_metrics()
    assert (
        'audit_sink_failure_total{sink="primary",tenant_id="tenant-metrics-fallback"} 1'
        in body
    )
    assert (
        'audit_sink_emit_total{outcome="ok",sink="fallback",'
        'tenant_id="tenant-metrics-fallback"} 1'
        in body
    )


def test_primary_failure_rate_gauge_updated():
    record_primary_sink_failure("tenant-gauge")
    record_primary_sink_failure("tenant-gauge")
    body = render_prometheus_metrics()
    assert (
        'audit_primary_sink_failures_per_minute{tenant_id="tenant-gauge"} 2'
        in body
    )
