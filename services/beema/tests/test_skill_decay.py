"""BEEMA Skill Decay v1 — clamp and ordering guards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.beema.analytics.skill_decay import (
    DECAY_CEIL,
    DECAY_FLOOR,
    SkillDecaySignal,
    SkillDecayUpdater,
)
from services.beema.ledger.interface import ConfirmationState, SessionLedgerRecord


def _mk_record(
    *,
    learner: str = "L1",
    case: str = "anaphylaxis_opd_001",
    score: float,
    when: datetime | None = None,
    session_suffix: str = "",
    replay_suffix: str = "",
) -> SessionLedgerRecord:
    when = when or datetime.now(timezone.utc)
    suffix = session_suffix or str(int(when.timestamp()))
    return SessionLedgerRecord(
        session_id=f"sess-{learner}-{suffix}",
        learner_pseudo_id=learner,
        cohort_id="cohort-A",
        case_id=case,
        case_version="1.0.0",
        physio_engine_version="physio@v1",
        rubric_version="1.0.0",
        replay_hash=f"replay-{suffix}-{replay_suffix}",
        finalized_at_utc=when.isoformat(),
        confirmation=ConfirmationState.UNCONFIRMED,
        preceptor_pseudo_id=None,
        grade_total_normalized=score,
        grade_passed=score >= 0.7,
        axis_normalized={"ACTION": score},
        evidence=(),
        flags=(),
        actions=(),
    )


def test_decay_signal_clamps_to_negative_one_floor():
    """
    Catastrophic regression across sessions must clamp decay_delta at DECAY_FLOOR.
    """
    updater = SkillDecayUpdater()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for i in range(5):
        updater.ingest(_mk_record(score=1.0, when=t0 + timedelta(days=i), replay_suffix=f"p{i}"))

    signal = None
    for i in range(5, 10):
        signal = updater.ingest(
            _mk_record(score=0.0, when=t0 + timedelta(days=i), replay_suffix=f"z{i}")
        )

    assert signal is not None
    assert isinstance(signal, SkillDecaySignal)
    assert signal.decay_delta >= DECAY_FLOOR
    assert signal.decay_delta <= DECAY_CEIL
    assert 0.0 <= signal.competency_after <= 1.0

    late = updater.ingest(
        _mk_record(score=0.0, when=t0 + timedelta(days=30), replay_suffix="late")
    )
    assert late is not None
    assert late.decay_delta >= DECAY_FLOOR
    assert 0.0 <= late.competency_after <= 1.0


def test_decay_signal_clamps_to_positive_ceil():
    """Rapid improvement cannot push decay_delta above DECAY_CEIL."""
    updater = SkillDecayUpdater()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for i in range(3):
        updater.ingest(_mk_record(score=0.1, when=t0 + timedelta(days=i), replay_suffix=f"lo{i}"))

    signal = None
    for i in range(3, 8):
        signal = updater.ingest(
            _mk_record(score=1.0, when=t0 + timedelta(days=i), replay_suffix=f"hi{i}")
        )

    assert signal is not None
    assert signal.decay_delta <= DECAY_CEIL
    assert 0.0 <= signal.competency_after <= 1.0


def test_decay_signal_ignores_out_of_order_records():
    """
    An older finalized_at must not produce negative time deltas that invert decay.
    """
    updater = SkillDecayUpdater()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    updater.ingest(_mk_record(score=0.8, when=t0 + timedelta(days=10), replay_suffix="new"))
    signal = updater.ingest(
        _mk_record(score=0.9, when=t0 + timedelta(days=2), replay_suffix="old")
    )

    if signal is not None:
        assert signal.decay_delta >= DECAY_FLOOR
        assert signal.time_since_last_s >= 0


def test_freshness_uses_policy_prior_basis():
    updater = SkillDecayUpdater()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    updater.ingest(_mk_record(score=0.9, when=t0, replay_suffix="a"))
    signal = updater.ingest(
        _mk_record(score=0.85, when=t0 + timedelta(days=60), replay_suffix="b")
    )
    assert signal is not None
    assert signal.basis == "policy_prior_v1"
    assert 0.0 <= signal.freshness <= 1.0
