"""Tests for BEEMA ledger read-only view contract."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest

from services.pratibimb.app.beema.ledger_view import (
    LEDGER_VIEW_VERSION,
    BEEMALedgerView,
    DateRange,
    InMemoryLedgerView,
    SessionSummary,
    UnitSafetyProfile,
)
from services.pratibimb.app.eval.error_dna import ErrorDNA


def _dna(**kwargs) -> ErrorDNA:
    defaults = dict(
        recognition_delay=0.9,
        action_sequencing=0.8,
        dose_accuracy=0.7,
        escalation_timing=0.6,
        recovery_velocity=0.5,
        communication_score=0.5,
    )
    defaults.update(kwargs)
    return ErrorDNA(**defaults)


def _summary(
    session_id: str = "s1",
    completed_at: datetime | None = None,
    overall: float = 0.80,
    dna: ErrorDNA | None = "DEFAULT",
) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        case_id="case1",
        case_version="1.0",
        completed_at=completed_at or datetime(2026, 8, 1),
        overall_score=overall,
        letter_grade="B",
        error_dna=_dna() if dna == "DEFAULT" else dna,
        physio_version="physio@abc123",
        grader_version="0.3.0",
        assessment_mode="formative",
    )


# ── Protocol conformance ─────────────────────────────────────────────────────

def test_in_memory_satisfies_protocol():
    view = InMemoryLedgerView()
    assert isinstance(view, BEEMALedgerView)


def test_protocol_is_read_only():
    """BEEMALedgerView protocol must not define any mutating methods."""
    methods = [
        name for name, _ in inspect.getmembers(BEEMALedgerView, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    mutating_prefixes = ("add", "set", "update", "delete", "remove", "write", "insert", "put")
    violations = [m for m in methods if any(m.startswith(p) for p in mutating_prefixes)]
    assert not violations, f"Protocol has mutating methods: {violations}"


def test_version():
    view = InMemoryLedgerView()
    assert view.version == LEDGER_VIEW_VERSION


# ── sessions_for ──────────────────────────────────────────────────────────────

def test_sessions_for_returns_sorted():
    view = InMemoryLedgerView()
    t1 = datetime(2026, 7, 1)
    t2 = datetime(2026, 8, 1)
    view.ingest(_summary(session_id="old", completed_at=t1))
    view.ingest(_summary(session_id="new", completed_at=t2))
    sessions = view.sessions_for("C001")
    assert sessions[0].session_id == "new"


def test_sessions_for_since_filter():
    view = InMemoryLedgerView()
    t1 = datetime(2026, 6, 1)
    t2 = datetime(2026, 8, 1)
    view.ingest(_summary(session_id="old", completed_at=t1))
    view.ingest(_summary(session_id="new", completed_at=t2))
    sessions = view.sessions_for("C001", since=datetime(2026, 7, 1))
    assert len(sessions) == 1
    assert sessions[0].session_id == "new"


def test_sessions_for_limit():
    view = InMemoryLedgerView()
    for i in range(20):
        view.ingest(_summary(
            session_id=f"s{i}",
            completed_at=datetime(2026, 8, 1) + timedelta(hours=i),
        ))
    assert len(view.sessions_for("C001", limit=5)) == 5


# ── ppv_history ───────────────────────────────────────────────────────────────

def test_ppv_history_returns_dna():
    view = InMemoryLedgerView()
    view.ingest(_summary(dna=_dna(recognition_delay=0.95)))
    history = view.ppv_history("C001")
    assert len(history) == 1
    assert history[0].recognition_delay == 0.95


def test_ppv_history_skips_none_dna():
    view = InMemoryLedgerView()
    view.ingest(_summary(dna=None))
    assert len(view.ppv_history("C001")) == 0


# ── unit_aggregate ────────────────────────────────────────────────────────────

def test_unit_aggregate_empty():
    view = InMemoryLedgerView()
    period = DateRange(datetime(2026, 1, 1), datetime(2026, 12, 31))
    profile = view.unit_aggregate("ICU-A", period)
    assert profile.session_count == 0
    assert profile.clinician_count == 0


def test_unit_aggregate_computes_means():
    view = InMemoryLedgerView()
    period = DateRange(datetime(2026, 7, 1), datetime(2026, 9, 1))
    view.ingest(_summary(overall=0.80, dna=_dna(escalation_timing=0.40)))
    view.ingest(_summary(
        session_id="s2",
        overall=0.60,
        dna=_dna(escalation_timing=0.50),
        completed_at=datetime(2026, 8, 15),
    ))
    profile = view.unit_aggregate("ICU-A", period)
    assert profile.session_count == 2
    assert profile.mean_overall == pytest.approx(0.70)
    assert profile.mean_error_dna["escalation_timing"] == pytest.approx(0.45)


def test_unit_aggregate_flags_weak_dimensions():
    view = InMemoryLedgerView()
    period = DateRange(datetime(2026, 7, 1), datetime(2026, 9, 1))
    view.ingest(_summary(dna=_dna(escalation_timing=0.30, recovery_velocity=0.40)))
    profile = view.unit_aggregate("ICU-A", period)
    assert "escalation_timing" in profile.dimensions_below_threshold
    assert "recovery_velocity" in profile.dimensions_below_threshold


# ── Governance: ingest is NOT on the Protocol ─────────────────────────────────

def test_ingest_not_on_protocol():
    """The ingest method is a test helper, not part of the read-only contract."""
    assert not hasattr(BEEMALedgerView, "ingest")
