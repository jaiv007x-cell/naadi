"""Ledger startup guards and full Session.finalize → BEEMA pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from services.beema.analytics.error_dna import ErrorDNAUpdater
from services.beema.analytics.skill_decay import SkillDecayUpdater
from services.beema.ingest.consumer import BeemaIngestConfig, BeemaIngestor
from services.beema.ingest.freshness_publisher import FreshnessBridge, InMemoryFreshnessPublisher
from services.beema.ledger.interface import LedgerQuery
from services.beema.state.cursor_store import InMemoryCursorStore
from services.pratibimb.app.case_gen.sampler import CaseSampler
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit
from services.pratibimb.app.eval.scorer import Turn
from services.pratibimb.app.physio.version_probe import get_version
from services.pratibimb.app.worker import Session
from services.pratibimb.ledger import db as ledger_db
from services.pratibimb.ledger.db import is_dev_environment
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger.startup import (
    LedgerStartupError,
    summative_case_ids,
    validate_ledger_startup,
)
from shared.schemas.session import AssessmentMode


ANAPHYLAXIS_BLUEPRINT = GradingBlueprint(
    case_id="anaphylaxis_01",
    case_version="v2.0",
    rubric_version="0.3.0",
    hits=(
        RubricHit(
            id="dx.anaphylaxis",
            axis=Axis.DIAGNOSTIC,
            matcher="diagnosis_stated",
            params={"dx": "anaphylaxis"},
            points=10.0,
        ),
        RubricHit(
            id="act.oxygen",
            axis=Axis.ACTION,
            matcher="order_placed",
            params={"order_id": "oxygen_nrb"},
            points=8.0,
        ),
    ),
)


@pytest.fixture
def enabled_ledger_db(monkeypatch, tmp_path):
    """Enable ledger append against a file-backed SQLite DB (shared across sessions)."""
    monkeypatch.delenv("LEDGER_DISABLED", raising=False)
    db_path = tmp_path / "ledger.db"
    monkeypatch.setenv("LEDGER_DATABASE_URL", f"sqlite:///{db_path}")
    ledger_db.get_engine.cache_clear()
    yield db_path
    ledger_db.get_engine.cache_clear()


def test_summative_case_scan(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps([
            {"case_id": "practice_case", "assessment_mode": "practice"},
            {"case_id": "exam_case", "assessment_mode": "summative"},
            {"case_id": "probe", "probe_only": True, "assessment_mode": "summative"},
        ]),
        encoding="utf-8",
    )
    assert summative_case_ids(corpus) == ["exam_case"]


def test_refuses_start_when_disabled_with_summative_cases(monkeypatch, tmp_path):
    monkeypatch.setenv("LEDGER_DISABLED", "1")
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps([{"case_id": "exam_case", "assessment_mode": "summative"}]),
        encoding="utf-8",
    )
    with pytest.raises(LedgerStartupError, match="summative cases"):
        validate_ledger_startup(corpus_path=corpus)


def test_warns_when_disabled_in_non_dev(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("LEDGER_DISABLED", "1")
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"case_id": "p1", "assessment_mode": "practice"}]), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        validate_ledger_startup(corpus_path=corpus)

    assert any("LEDGER_DISABLED is set" in r.message for r in caplog.records)
    assert not is_dev_environment()


def test_session_finalize_to_beema_skill_decay(enabled_ledger_db, monkeypatch):
    """
    Anaphylaxis-shaped session → Nirikshak grade → ledger append → BEEMA drain
    → SkillDecaySignal. Proves the credential spine closes end-to-end.
    """
    case = CaseSampler().sample()
    session = Session(
        "sess-pipeline-e2e",
        case,
        speaker_wav="",
        assessment_mode=AssessmentMode.SUMMATIVE,
        learner_id="L-pipeline",
        cohort_id="cohortA",
    )

    engine = session.physio
    engine._flag_history.extend([
        {
            "flag": "hypotension",
            "set": True,
            "t": 55.0,
            "cause": "drug.pd_effect",
            "namespace": "drug",
            "detail": {"drug": "morphine"},
        },
        {
            "flag": "hypotension",
            "set": False,
            "t": 120.0,
            "cause": "physiology.homeostasis",
            "namespace": "physiology",
            "detail": {},
            "reason": "flag_expired_or_resolved",
        },
    ])
    session.turns.extend([
        Turn(speaker="learner", text="oxygen", ts=40.0, action="order:oxygen_nrb"),
        Turn(speaker="learner", text="anaphylaxis", ts=70.0, action="dx:anaphylaxis"),
    ])

    grade = session.finalize(ANAPHYLAXIS_BLUEPRINT)
    assert grade is not None
    assert grade.passed is True
    assert session.ledger_replay_hash is not None
    assert session.ledger_replay_hash.startswith("sha256:")

    physio_version = get_version()
    with ledger_db.ledger_session() as db:
        reader = SqlLedgerReader(db)
        page = reader.query(LedgerQuery(cohort_id="cohortA", limit=10))
        assert len(page.records) == 1
        rec = page.records[0]
        assert rec.session_id == "sess-pipeline-e2e"
        assert rec.case_id == session.case.case_id
        assert rec.rubric_version == ANAPHYLAXIS_BLUEPRINT.rubric_version
        assert rec.physio_engine_version == physio_version
        assert any(e.hit_id == "dx.anaphylaxis" and e.matched for e in rec.evidence)
        assert any(f.flag == "hypotension" for f in rec.flags)

        skill_decay = SkillDecayUpdater()
        publisher = InMemoryFreshnessPublisher()
        bridge = FreshnessBridge(publisher)
        ingestor = BeemaIngestor(
            reader=reader,
            cursor_store=InMemoryCursorStore(),
            skill_decay=skill_decay,
            error_dna=ErrorDNAUpdater(),
            cfg=BeemaIngestConfig(
                cohort_ids=("cohortA",),
                beema_trusted_physio_versions=frozenset({physio_version}),
                accept_unconfirmed=True,
                page_size=10,
            ),
            freshness_bridge=bridge,
        )
        assert ingestor.run_once() == 1
        assert skill_decay.last_processed() == "sess-pipeline-e2e"
        assert len(skill_decay.signals) >= 1
        assert skill_decay.signals[0].source_session_id == "sess-pipeline-e2e"
        assert len(publisher.snapshots) >= 1
        snap = publisher.snapshots[0]
        assert snap.learner_pseudo_id == "L-pipeline"
        assert snap.basis == "policy_prior_v1"
        state = publisher.states[(snap.learner_pseudo_id, snap.competency_id)]
        assert state.ability == snap.ability
        assert state.freshness == snap.freshness
        assert state.confidence == snap.confidence


def test_finalize_skips_ledger_when_disabled(monkeypatch, tmp_path):
    """LEDGER_DISABLED=1: grade succeeds, no ledger row appended."""
    monkeypatch.setenv("LEDGER_DISABLED", "1")
    db_path = tmp_path / "ledger.db"
    monkeypatch.setenv("LEDGER_DATABASE_URL", f"sqlite:///{db_path}")
    ledger_db.get_engine.cache_clear()

    case = CaseSampler().sample()
    session = Session(
        "sess-no-ledger",
        case,
        speaker_wav="",
        assessment_mode=AssessmentMode.SUMMATIVE,
        learner_id="L1",
        cohort_id="cohortA",
    )
    session.turns.append(
        Turn(speaker="learner", text="anaphylaxis", ts=70.0, action="dx:anaphylaxis")
    )

    grade = session.finalize(ANAPHYLAXIS_BLUEPRINT)
    assert grade is not None
    assert session.ledger_replay_hash is None

    with ledger_db.ledger_session() as db:
        page = SqlLedgerReader(db).query(LedgerQuery(cohort_id="cohortA", limit=10))
        assert len(page.records) == 0

    ledger_db.get_engine.cache_clear()
