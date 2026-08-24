"""Tests for JsonlFallbackSink — append-only audit capture on primary sink failure."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.pratibimb.audit.sink import AuditEvent
from services.pratibimb.ledger_read.audit_decorator import safe_emit
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink

UTC = timezone.utc
TENANT = "tenant_aiims_delhi"


def _event(
    *,
    query_id: str = "q-1",
    tenant_id: str = TENANT,
    at_utc: datetime | None = None,
) -> AuditEvent:
    return AuditEvent(
        query_id=query_id,
        at_utc=at_utc or datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        tenant_id=tenant_id,
        subject_pseudo_id="learner-p-42",
        caller_kind="preceptor",
        scope="preceptor.review",
        query_kind="learner_evidence",
        query_params_hash="abc" * 16 + "abcd",
        query_params_bytes=128,
        outcome="ok",
        duration_ms=5,
        result_row_count=2,
    )


@pytest.fixture
def fallback(tmp_path) -> JsonlFallbackSink:
    return JsonlFallbackSink(tmp_path)


def test_happy_write(fallback: JsonlFallbackSink):
    evt = _event()
    fallback.emit(evt, fallback_reason="OperationalError")
    day = evt.at_utc.strftime("%Y-%m-%d")
    path = fallback.root / TENANT / f"{day}.jsonl"
    assert path.exists()
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["query_id"] == "q-1"
    assert row["tenant_id"] == TENANT
    assert row["outcome"] == "ok"
    assert row["fallback_reason"] == "OperationalError"


def test_fsync_called_before_close(fallback: JsonlFallbackSink):
    order: list[str] = []

    def track_fsync(fd: int) -> None:
        order.append("fsync")

    def track_close(fd: int) -> None:
        order.append("close")

    with patch("services.pratibimb.ledger_read.fallback_sink.os.fsync", side_effect=track_fsync):
        with patch("services.pratibimb.ledger_read.fallback_sink.os.close", side_effect=track_close):
            fallback.emit(_event())
    assert order == ["fsync", "close"]


def test_day_rollover(fallback: JsonlFallbackSink):
    before = datetime(2026, 8, 19, 23, 59, tzinfo=UTC)
    after = datetime(2026, 8, 20, 0, 1, tzinfo=UTC)
    fallback.emit(_event(query_id="q-before", at_utc=before))
    fallback.emit(_event(query_id="q-after", at_utc=after))
    assert (fallback.root / TENANT / "2026-08-19.jsonl").exists()
    assert (fallback.root / TENANT / "2026-08-20.jsonl").exists()
    ids_before = {r["query_id"] for r in fallback.read_lines(TENANT, "2026-08-19")}
    ids_after = {r["query_id"] for r in fallback.read_lines(TENANT, "2026-08-20")}
    assert ids_before == {"q-before"}
    assert ids_after == {"q-after"}


def test_tenant_isolation(fallback: JsonlFallbackSink):
    day = "2026-08-20"
    fallback.emit(_event(query_id="a-1", tenant_id="tenant_a"))
    fallback.emit(_event(query_id="b-1", tenant_id="tenant_b"))
    a_lines = fallback.read_lines("tenant_a", day)
    b_lines = fallback.read_lines("tenant_b", day)
    assert len(a_lines) == 1
    assert len(b_lines) == 1
    assert a_lines[0]["query_id"] == "a-1"
    assert b_lines[0]["query_id"] == "b-1"
    assert a_lines[0]["tenant_id"] != b_lines[0]["tenant_id"]


def test_sink_of_sink_failure_sets_marker(fallback: JsonlFallbackSink):
    assert fallback.is_healthy() is True
    with patch(
        "services.pratibimb.ledger_read.fallback_sink.os.open",
        side_effect=OSError("disk full"),
    ):
        fallback.emit(_event())
    assert fallback.is_healthy(within_seconds=3600) is False


def test_fallback_reason_via_safe_emit(tmp_path):
    fallback = JsonlFallbackSink(tmp_path)
    evt = _event()
    primary = MagicMock()
    primary.emit.side_effect = RuntimeError("db down")

    safe_emit(primary, evt, fallback=fallback)

    day = evt.at_utc.strftime("%Y-%m-%d")
    rows = fallback.read_lines(TENANT, day)
    assert len(rows) == 1
    assert rows[0]["fallback_reason"] == "RuntimeError"
    assert rows[0]["query_id"] == "q-1"
