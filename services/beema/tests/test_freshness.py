"""Dhaara Freshness v1 — snapshot projection and competency state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.beema.analytics.freshness import (
    competency_state_from_snapshot,
    snapshot_from_signal,
)
from services.beema.analytics.skill_decay import SkillDecaySignal, SkillDecayUpdater
from services.beema.ingest.freshness_publisher import FreshnessBridge, InMemoryFreshnessPublisher
from services.beema.ledger.interface import ConfirmationState, SessionLedgerRecord
from shared.schemas.freshness import FRESHNESS_BASIS_V1
from shared.schemas.timeline import TimelineEventKind, project_snapshot_to_timeline


def _mk_record(
    *,
    learner: str = "L1",
    score: float = 0.8,
    when: datetime | None = None,
    session_id: str = "sess-1",
    replay_hash: str = "replay-1",
    axis: dict | None = None,
) -> SessionLedgerRecord:
    when = when or datetime(2026, 3, 14, tzinfo=timezone.utc)
    return SessionLedgerRecord(
        session_id=session_id,
        learner_pseudo_id=learner,
        cohort_id="cohort-A",
        case_id="stemi_01",
        case_version="1.0.0",
        physio_engine_version="physio@v1",
        rubric_version="1.0.0",
        replay_hash=replay_hash,
        finalized_at_utc=when.isoformat(),
        confirmation=ConfirmationState.UNCONFIRMED,
        preceptor_pseudo_id=None,
        grade_total_normalized=score,
        grade_passed=score >= 0.7,
        axis_normalized=axis or {"DIAGNOSTIC": score},
        evidence=(),
        flags=(),
        actions=(),
    )


def test_snapshot_separates_ability_confidence_freshness():
    sig = SkillDecaySignal(
        learner_id="L1",
        competency_id="DIAGNOSTIC",
        effective_score=0.78,
        confidence_lower=0.62,
        days_since_practice=71.0,
        stale=True,
        source_session_id="sess-stemi",
        source_replay_hash="sha256:abc",
        competency_before=0.70,
        competency_after=0.78,
        freshness=0.63,
        evidence_age_days=71.0,
        reassessment_due=True,
        basis=FRESHNESS_BASIS_V1,
    )
    snap = snapshot_from_signal(sig, source_session_ids=("sess-stemi",))
    assert snap.ability == pytest.approx(0.78)
    assert snap.confidence > snap.freshness
    assert snap.freshness == pytest.approx(0.63)
    assert snap.reassessment_due is True
    assert snap.basis == FRESHNESS_BASIS_V1
    assert snap.source_session_ids == ("sess-stemi",)


def test_competency_state_from_snapshot():
    sig = SkillDecaySignal(
        learner_id="L1",
        competency_id="DIAGNOSTIC",
        effective_score=0.78,
        confidence_lower=0.66,
        days_since_practice=71.0,
        stale=True,
        source_session_id="sess-stemi",
        source_replay_hash="sha256:abc",
        freshness=0.63,
        evidence_age_days=71.0,
        reassessment_due=True,
    )
    snap = snapshot_from_signal(sig)
    state = competency_state_from_snapshot(snap)
    assert state.ability == pytest.approx(0.78)
    assert state.confidence != state.ability
    assert state.freshness == pytest.approx(0.63)
    assert state.reassessment_due is True
    assert state.last_session_id == "sess-stemi"


def test_freshness_bridge_tracks_source_session_ids():
    publisher = InMemoryFreshnessPublisher()
    updater = SkillDecayUpdater(on_signal=FreshnessBridge(publisher).as_callback())
    bridge = FreshnessBridge(publisher, store=updater.store)
    updater.on_signal = bridge.as_callback()

    t0 = datetime(2026, 3, 14, tzinfo=timezone.utc)
    updater.ingest(_mk_record(session_id="s1", replay_hash="r1", when=t0, score=0.7))
    updater.ingest(
        _mk_record(
            session_id="s2",
            replay_hash="r2",
            when=t0 + timedelta(days=30),
            score=0.78,
        )
    )

    assert len(publisher.snapshots) == 2
    latest = publisher.snapshots[-1]
    assert latest.source_session_ids == ("s1", "s2")
    state = publisher.states[("L1", "DIAGNOSTIC")]
    assert state.ability == pytest.approx(0.78)
    assert state.source_session_ids == ("s1", "s2")


def test_timeline_projection_links_provenance():
    sig = SkillDecaySignal(
        learner_id="L1",
        competency_id="DIAGNOSTIC",
        effective_score=0.78,
        confidence_lower=0.66,
        days_since_practice=71.0,
        stale=True,
        source_session_id="sess-stemi",
        source_replay_hash="sha256:abc",
        competency_before=0.70,
        competency_after=0.78,
        freshness=0.63,
        evidence_age_days=71.0,
        reassessment_due=True,
    )
    snap = snapshot_from_signal(sig)
    entries = project_snapshot_to_timeline(snap)
    kinds = {e.kind for e in entries}
    assert TimelineEventKind.SIMULATION_PASSED in kinds
    assert TimelineEventKind.REASSESSMENT_DUE in kinds
    for e in entries:
        if e.session_id is not None:
            assert e.session_id == "sess-stemi"
        if e.replay_hash is not None:
            assert e.replay_hash == "sha256:abc"
