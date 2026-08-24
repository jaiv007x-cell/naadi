from __future__ import annotations

import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from services.beema.analytics.error_dna import ErrorDNAUpdater
from services.beema.analytics.skill_decay import SkillDecayUpdater
from services.beema.ingest.consumer import BeemaIngestConfig, BeemaIngestor
from services.beema.ledger.interface import ConfirmationState, LedgerQuery
from services.beema.state.cursor_store import InMemoryCursorStore
from dataclasses import replace

from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit, Severity
from services.pratibimb.app.eval.scorer import Turn
from shared.schemas.flag_cause import FlagCause
from services.pratibimb.app.physio.state import PhysioTrace as LegacyPhysioTrace
from services.pratibimb.app.physio.version_probe import get_version
from services.pratibimb.ledger.db import seed_trusted_physio_version
from services.pratibimb.ledger.models import Base, SessionLedgerRow
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger.writer import (
    LedgerConflictError,
    SqlLedgerWriter,
    UntrustedPhysioError,
    append_graded_session,
    build_record,
    replay_hash,
)
from shared.schemas.trace import DiagnosisEvent


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("LEDGER_DISABLED", raising=False)
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    seed_trusted_physio_version(engine, approved_by="test")
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        yield session


def _legacy_trace_with_flags() -> LegacyPhysioTrace:
    trace = LegacyPhysioTrace()
    trace.set_flag(
        "hypotension",
        60.0,
        cause=FlagCause.DRUG_PD_EFFECT,
        detail={"drug": "morphine"},
    )
    trace.clear_flag(
        "hypotension",
        180.0,
        cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS,
    )
    trace.add_event(45.0, "dose", {"drug": "morphine", "route": "IV", "amount_mg": 10})
    trace.add_event(80.0, "order", {"order": "oxygen_nrb"})
    trace.add_event(80.0, "diagnosis", {"dx": "anaphylaxis", "confidence": 0.9})
    return trace


def _blueprint() -> GradingBlueprint:
    return GradingBlueprint(
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
                id="act.epinephrine",
                axis=Axis.ACTION,
                matcher="drug_given",
                params={"drug_id": "epinephrine"},
                points=15.0,
                required=True,
                fail_case_on_violation=True,
                severity=Severity.CRITICAL,
            ),
        ),
    )


def _grade_and_turns(
    legacy: LegacyPhysioTrace,
    blueprint: GradingBlueprint,
) -> tuple[CaseGrade, list[Turn]]:
    typed, _report = legacy.to_typed_trace(
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
    )
    typed.record_diagnosis(DiagnosisEvent(dx="anaphylaxis", confidence=0.9, t_s=80.0))
    grade = Nirikshak(blueprint, typed).grade()
    turns = [
        Turn(speaker="learner", text="anaphylaxis", ts=80.0, action="dx:anaphylaxis"),
    ]
    return grade, turns


def test_build_record_defaults_blueprint_content_hash_none(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    record = build_record(
        session_id="sess-null-hash",
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
    )
    assert record.blueprint_content_hash is None


def test_append_and_read_roundtrip_blueprint_content_hash(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    stamped = "a" * 64

    record = build_record(
        session_id="sess-hash",
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash=stamped,
    )
    SqlLedgerWriter(db).append(record)

    row = db.get(SessionLedgerRow, "sess-hash")
    assert row is not None
    assert row.blueprint_content_hash == stamped

    reader = SqlLedgerReader(db)
    got = reader.get("sess-hash")
    assert got is not None
    assert got.blueprint_content_hash == stamped
    assert got.replay_hash.startswith("sha256:")


def test_append_idempotent_when_blueprint_content_hash_matches(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    stamped = "b" * 64
    record = build_record(
        session_id="sess-idem-hash",
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash=stamped,
    )
    first = SqlLedgerWriter(db).append(record)
    second = SqlLedgerWriter(db).append(record)
    assert first == second


def test_append_conflict_when_blueprint_content_hash_differs(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    base = dict(
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
    )
    SqlLedgerWriter(db).append(
        build_record(session_id="sess-hash-conflict", blueprint_content_hash="c" * 64, **base)
    )
    with pytest.raises(LedgerConflictError):
        SqlLedgerWriter(db).append(
            build_record(
                session_id="sess-hash-conflict",
                blueprint_content_hash="d" * 64,
                **base,
            )
        )


def test_blueprint_content_hash_check_rejects_wrong_length(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    record = build_record(
        session_id="sess-bad-len",
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash="too-short",
    )
    with pytest.raises(Exception):
        SqlLedgerWriter(db).append(record)



def test_replay_hash_is_deterministic():
    legacy = _legacy_trace_with_flags()
    assert replay_hash(legacy) == replay_hash(legacy)


def test_replay_hash_changes_on_trace_change():
    baseline = _legacy_trace_with_flags()
    changed = _legacy_trace_with_flags()
    changed.add_event(200.0, "dose", {"drug": "epinephrine", "route": "IM", "amount_mg": 0.3})
    assert replay_hash(changed) != replay_hash(baseline)


def test_append_writes_row(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)

    replay = append_graded_session(
        db,
        session_id="sess-001",
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
    )

    row = db.get(SessionLedgerRow, "sess-001")
    assert row is not None
    assert row.replay_hash == replay
    assert row.grade_passed is False
    assert row.evidence[0]["hit_id"] == "dx.anaphylaxis"


def test_append_is_idempotent(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    kwargs = dict(
        session_id="sess-001",
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
    )
    first = append_graded_session(db, **kwargs)
    second = append_graded_session(db, **kwargs)
    assert first == second
    assert len(db.execute(select(SessionLedgerRow)).scalars().all()) == 1


def test_append_conflict_on_different_content(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    append_graded_session(
        db,
        session_id="sess-001",
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
    )

    legacy2 = _legacy_trace_with_flags()
    legacy2.add_event(200.0, "dose", {"drug": "epinephrine", "route": "IM", "amount_mg": 0.3})
    grade2, turns2 = _grade_and_turns(legacy2, blueprint)

    with pytest.raises(LedgerConflictError):
        append_graded_session(
            db,
            session_id="sess-001",
            learner_pseudo_id="learner-42",
            cohort_id="cohortA",
            case_id=blueprint.case_id,
            case_version=blueprint.case_version,
            grade=grade2,
            blueprint=blueprint,
            legacy_trace=legacy2,
            turns=turns2,
        )


def test_untrusted_physio_rejected(db):
    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    bad_grade = replace(grade, physio_version="physio@unknown")
    record = build_record(
        session_id="sess-x",
        learner_pseudo_id="L1",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=bad_grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
    )

    with pytest.raises(UntrustedPhysioError):
        SqlLedgerWriter(db).append(record)


def test_end_to_end_grade_append_read_ingest_skill_decay(db, monkeypatch):
    monkeypatch.delenv("LEDGER_DISABLED", raising=False)

    legacy = _legacy_trace_with_flags()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    physio_version = get_version()

    append_graded_session(
        db,
        session_id="sess-e2e",
        learner_pseudo_id="L1",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        confirmation=ConfirmationState.UNCONFIRMED,
    )

    reader = SqlLedgerReader(db)
    page = reader.query(LedgerQuery(cohort_id="cohortA", limit=10))
    assert len(page.records) == 1
    assert page.records[0].session_id == "sess-e2e"
    assert page.records[0].physio_engine_version == physio_version

    cursor_store = InMemoryCursorStore()
    skill_decay = SkillDecayUpdater()
    error_dna = ErrorDNAUpdater()
    ingestor = BeemaIngestor(
        reader=reader,
        cursor_store=cursor_store,
        skill_decay=skill_decay,
        error_dna=error_dna,
        cfg=BeemaIngestConfig(
            cohort_ids=("cohortA",),
            beema_trusted_physio_versions=frozenset({physio_version}),
            accept_unconfirmed=True,
            page_size=10,
        ),
    )

    processed = ingestor.run_once()
    assert processed == 1
    assert skill_decay.last_processed() == "sess-e2e"
    assert error_dna.last_processed() == "sess-e2e"
    assert len(skill_decay.signals) >= 1
    assert skill_decay.signals[0].source_session_id == "sess-e2e"
