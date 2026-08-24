"""BEEMA Error-DNA clustering over ledger records."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.beema.analytics.error_dna import ErrorDNAUpdater
from services.beema.ledger.interface import (
    ConfirmationState,
    FlagEpisode,
    SessionLedgerRecord,
    TypedEvidence,
)
from shared.schemas.flag_cause import FlagCause
from shared.schemas.flag_causes import FlagClearReason


def _rec(
    session_id: str,
    learner: str = "L1",
    *,
    flags: tuple = (),
    evidence: tuple = (),
) -> SessionLedgerRecord:
    return SessionLedgerRecord(
        session_id=session_id,
        learner_pseudo_id=learner,
        cohort_id="cohort-A",
        case_id="anaphylaxis_01",
        case_version="1.0.0",
        physio_engine_version="physio@v1",
        rubric_version="1.0.0",
        replay_hash=f"hash-{session_id}",
        finalized_at_utc=datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat(),
        confirmation=ConfirmationState.UNCONFIRMED,
        preceptor_pseudo_id=None,
        grade_total_normalized=0.5,
        grade_passed=False,
        axis_normalized={"SAFETY": 0.4},
        evidence=evidence,
        flags=flags,
        actions=(),
    )


def test_clusters_flag_episodes_by_cause_reason():
    updater = ErrorDNAUpdater()
    flag = FlagEpisode(
        flag="hypotension",
        cause="drug.morphine",
        set_at_s=60.0,
        cleared_at_s=180.0,
        clear_reason=FlagClearReason.RESOLVED,
        caused_by_action_id="give_morphine",
    )
    updater.update_from_session(_rec("s1", flags=(flag,)))
    updater.update_from_session(_rec("s2", "L2", flags=(flag,)))

    clusters = updater.top_clusters()
    assert len(clusters) == 1
    c = clusters[0]
    assert c.kind == "flag_episode"
    assert c.cause == "drug.morphine"
    assert c.session_count == 2
    assert c.learner_count == 2
    assert "hypotension" in c.flags


def test_clusters_critical_rubric_misses():
    updater = ErrorDNAUpdater()
    ev = TypedEvidence(
        hit_id="safe.no_iv_epi_bolus",
        axis="safety",
        matcher="drug_given",
        matched=False,
        awarded=0.0,
        weight=10.0,
        at_sim_time_s=90.0,
        critical=True,
    )
    updater.update_from_session(_rec("s1", evidence=(ev,)))
    updater.update_from_session(_rec("s2", evidence=(ev,)))

    miss = [c for c in updater.top_clusters() if c.kind == "critical_miss"]
    assert len(miss) == 1
    assert miss[0].reason == "safe.no_iv_epi_bolus"
    assert miss[0].session_count == 2


def test_idempotent_on_session_id():
    updater = ErrorDNAUpdater()
    flag = FlagEpisode(
        flag="hypotension",
        cause=FlagCause.DRUG_PD_EFFECT,
        set_at_s=1.0,
        cleared_at_s=None,
        clear_reason=None,
    )
    rec = _rec("s1", flags=(flag,))
    updater.update_from_session(rec)
    updater.update_from_session(rec)
    assert updater.clusters[list(updater.clusters.keys())[0]].session_count == 1


def test_mixed_flag_and_critical_clusters_ranked_by_count():
    updater = ErrorDNAUpdater()
    flag = FlagEpisode(
        flag="hypotension",
        cause="drug.morphine",
        set_at_s=1.0,
        cleared_at_s=2.0,
        clear_reason=None,
    )
    miss = TypedEvidence(
        hit_id="safe.no_iv_epi_bolus",
        axis="safety",
        matcher="drug_given",
        matched=False,
        awarded=0.0,
        weight=10.0,
        at_sim_time_s=1.0,
        critical=True,
    )
    for sid in ("a", "b", "c"):
        updater.update_from_session(_rec(sid, flags=(flag,)))
    updater.update_from_session(_rec("d", evidence=(miss,)))

    top = updater.top_clusters(limit=2)
    assert top[0].session_count >= top[1].session_count
    assert top[0].session_count == 3


def test_occurrence_count_vs_session_count():
    """Twenty flags in one session ≠ twenty independent sessions."""
    updater = ErrorDNAUpdater()
    flag = FlagEpisode(
        flag="hypotension",
        cause="drug.morphine",
        set_at_s=1.0,
        cleared_at_s=2.0,
        clear_reason=None,
    )
    rec = _rec("s1", flags=tuple(flag for _ in range(20)))
    updater.update_from_session(rec)
    updater.update_from_session(_rec("s2", flags=(flag,)))
    updater.update_from_session(_rec("s3", flags=(flag,)))

    clusters = updater.top_clusters()
    assert len(clusters) == 1
    c = clusters[0]
    assert c.occurrence_count == 22
    assert c.session_count == 3
    assert len(c.session_ids) == 3


def test_session_recurrence_ranks_above_occurrence_spike():
    """Three independent sessions outrank one session with many occurrences."""
    updater = ErrorDNAUpdater()
    flag_a = FlagEpisode(
        flag="hypotension",
        cause="drug.morphine",
        set_at_s=1.0,
        cleared_at_s=2.0,
        clear_reason=None,
    )
    flag_b = FlagEpisode(
        flag="arrhythmia",
        cause="physiology.homeostasis",
        set_at_s=1.0,
        cleared_at_s=2.0,
        clear_reason=None,
    )
    updater.update_from_session(_rec("dense", flags=tuple(flag_a for _ in range(15))))
    for sid in ("x", "y", "z"):
        updater.update_from_session(_rec(sid, flags=(flag_b,)))

    top = updater.top_clusters(limit=2)
    assert top[0].cause == "physiology.homeostasis"
    assert top[0].session_count == 3
    assert top[0].occurrence_count == 3
    assert top[1].session_count == 1
    assert top[1].occurrence_count == 15
