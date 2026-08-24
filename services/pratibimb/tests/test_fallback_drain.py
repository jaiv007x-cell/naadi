"""Tests for fallback JSONL drain — idempotency, lag, batching, crash recovery."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.audit.metrics import render_prometheus_metrics
from services.pratibimb.audit.models_audit import LedgerReadAuditRow
from services.pratibimb.audit.sink import AuditEvent, SqlAuditSink
from services.pratibimb.ledger.models import Base
from services.pratibimb.ledger_read.fallback_drain import (
    FallbackDrainWorker,
    SessionPrimaryHealthCheck,
)
import services.pratibimb.audit.models_audit  # noqa: F401

UTC = timezone.utc
T0 = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=2)


@dataclass
class _MemoryPrimary:
    events: dict[str, AuditEvent] = field(default_factory=dict)
    healthy: bool = True
    write_calls: dict[str, int] = field(default_factory=dict)

    def emit_idempotent(self, event: AuditEvent) -> bool:
        self.write_calls[event.query_id] = self.write_calls.get(event.query_id, 0) + 1
        self.events[event.query_id] = event
        return True

    def is_healthy(self) -> bool:
        return self.healthy


def _audit_row(
    query_id: str,
    *,
    tenant_id: str = "tenant-a",
    at_utc: datetime = T0,
) -> dict:
    return {
        "query_id": query_id,
        "at_utc": at_utc.isoformat(),
        "tenant_id": tenant_id,
        "subject_pseudo_id": "L1",
        "caller_kind": "preceptor",
        "scope": "preceptor.review",
        "query_kind": "learner_evidence",
        "query_params_hash": "abc",
        "query_params_bytes": 3,
        "outcome": "ok",
        "duration_ms": 5,
        "result_row_count": 1,
        "fallback_reason": "OperationalError",
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8",
    )


@pytest.fixture
def tmp_root(tmp_path) -> Path:
    return tmp_path / "fallback"


@pytest.fixture
def primary() -> _MemoryPrimary:
    return _MemoryPrimary()


@pytest.fixture
def worker(tmp_root, primary) -> FallbackDrainWorker:
    return FallbackDrainWorker(
        root_dir=tmp_root,
        primary=primary,
        primary_health=primary,
        batch_size_per_tenant=1000,
        clock=lambda: T1,
    )


def test_happy_path_drains_to_primary_and_marks_sidecar(worker, tmp_root, primary):
    jsonl = tmp_root / "tenant-a" / "2026-08-19.jsonl"
    _write_jsonl(jsonl, [_audit_row("q1"), _audit_row("q2")])

    result = worker.drain_tick()
    assert result.records_drained == 2
    assert set(primary.events) == {"q1", "q2"}

    marker = jsonl.with_name(jsonl.name + ".drained")
    assert marker.read_text(encoding="utf-8").splitlines() == ["q1", "q2"]
    assert jsonl.read_text(encoding="utf-8").count("\n") == 2


def test_primary_unhealthy_skips_drain(worker, primary, tmp_root):
    primary.healthy = False
    _write_jsonl(tmp_root / "tenant-a" / "2026-08-19.jsonl", [_audit_row("q1")])

    result = worker.drain_tick()
    assert result.skipped_reason == "primary_sink_unhealthy"
    assert primary.events == {}


def test_batch_size_limits_per_tenant(tmp_root, primary):
    worker = FallbackDrainWorker(
        root_dir=tmp_root,
        primary=primary,
        primary_health=primary,
        batch_size_per_tenant=2,
        clock=lambda: T1,
    )
    rows = [_audit_row(f"q{i}") for i in range(5)]
    _write_jsonl(tmp_root / "tenant-a" / "2026-08-19.jsonl", rows)

    first = worker.drain_tick()
    assert first.records_drained == 2
    assert len(primary.events) == 2

    second = worker.drain_tick()
    assert second.records_drained == 2
    assert len(primary.events) == 4


def test_lag_metric_reflects_oldest_undrained(worker, tmp_root):
    _write_jsonl(
        tmp_root / "tenant-a" / "2026-08-19.jsonl",
        [_audit_row("q-old", at_utc=T0)],
    )
    worker.update_lag_metrics(["tenant-a"])
    body = render_prometheus_metrics()
    # T1 - T0 = 2 hours = 7200 seconds
    assert 'audit_fallback_drain_lag_seconds{tenant_id="tenant-a"} 7200' in body

    worker.drain_tick()
    worker.update_lag_metrics(["tenant-a"])
    body = render_prometheus_metrics()
    assert 'audit_fallback_drain_lag_seconds{tenant_id="tenant-a"} 0' in body


def test_crash_between_write_and_marker_is_idempotent(tmp_root):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sql_sink = SqlAuditSink(SessionLocal)
    health = SessionPrimaryHealthCheck(SessionLocal)

    jsonl = tmp_root / "tenant-a" / "2026-08-19.jsonl"
    _write_jsonl(
        jsonl,
        [_audit_row("q1"), _audit_row("q2"), _audit_row("q3")],
    )

    worker = FallbackDrainWorker(
        root_dir=tmp_root,
        primary=sql_sink,
        primary_health=health,
        clock=lambda: T1,
    )

    crash = {"armed": True}

    def after_write(query_id: str) -> None:
        if query_id == "q2" and crash["armed"]:
            crash["armed"] = False
            raise RuntimeError("simulated crash before drained marker")

    with pytest.raises(RuntimeError, match="simulated crash"):
        worker.drain_file(jsonl, limit=1000, after_write=after_write)

    marker = jsonl.with_name(jsonl.name + ".drained")
    assert marker.read_text(encoding="utf-8").splitlines() == ["q1"]

    with SessionLocal() as session:
        count_after_crash = session.execute(
            select(func.count()).select_from(LedgerReadAuditRow)
        ).scalar_one()
    assert count_after_crash == 2

    # Resume drain — q2 must not duplicate, q3 lands once.
    worker.drain_tick()
    with SessionLocal() as session:
        rows = session.execute(select(LedgerReadAuditRow)).scalars().all()
    assert len(rows) == 3
    assert {r.query_id for r in rows} == {"q1", "q2", "q3"}
    assert sql_sink.emit_idempotent(worker.event_from_row(_audit_row("q2"))) is True
    with SessionLocal() as session:
        assert (
            session.execute(
                select(func.count()).select_from(LedgerReadAuditRow)
            ).scalar_one()
            == 3
        )
