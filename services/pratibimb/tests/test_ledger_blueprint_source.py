"""E2.2.c: session_ledger.blueprint_source provenance (I-E22-3, I-E22-4)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit, Severity
from services.pratibimb.app.eval.scorer import Turn
from services.pratibimb.app.physio.state import PhysioTrace as LegacyPhysioTrace
from services.pratibimb.ledger.db import seed_trusted_physio_version
from services.pratibimb.ledger.models import Base, SessionLedgerRow, TrustedPhysioVersion
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger.writer import (
    BLUEPRINT_SOURCE_PUBLISHED,
    BLUEPRINT_SOURCE_SEED,
    LedgerConflictError,
    SqlLedgerWriter,
    build_record,
    validate_blueprint_provenance,
)
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.flag_cause import FlagCause
from shared.schemas.ledger_read import ConsentScope, LearnerEvidenceFilter
from shared.schemas.trace import DiagnosisEvent

HASH = "a" * 64


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("LEDGER_DISABLED", raising=False)
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    seed_trusted_physio_version(engine, approved_by="test")
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        yield session


def _legacy() -> LegacyPhysioTrace:
    trace = LegacyPhysioTrace()
    trace.set_flag(
        "hypotension",
        60.0,
        cause=FlagCause.DRUG_PD_EFFECT,
        detail={"drug": "morphine"},
    )
    trace.clear_flag("hypotension", 180.0, cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS)
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


def _grade_and_turns(legacy, blueprint):
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


def _build(
    db,
    session_id: str,
    *,
    blueprint_content_hash: str | None = None,
    blueprint_source: str | None = None,
):
    legacy = _legacy()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)
    return build_record(
        session_id=session_id,
        learner_pseudo_id="learner-42",
        cohort_id="cohortA",
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash=blueprint_content_hash,
        blueprint_source=blueprint_source,
    )


def test_published_backed_row_has_source_and_hash(db):
    record = _build(
        db,
        "sess-pub",
        blueprint_content_hash=HASH,
        blueprint_source=BLUEPRINT_SOURCE_PUBLISHED,
    )
    SqlLedgerWriter(db).append(record)
    row = db.get(SessionLedgerRow, "sess-pub")
    assert row is not None
    assert row.blueprint_source == "published"
    assert row.blueprint_content_hash == HASH
    got = SqlLedgerReader(db).get("sess-pub")
    assert got is not None
    assert got.blueprint_source == "published"
    assert got.blueprint_content_hash == HASH


def test_seed_fallback_row_has_source_seed_and_null_hash(db):
    record = _build(
        db,
        "sess-seed",
        blueprint_content_hash=None,
        blueprint_source=BLUEPRINT_SOURCE_SEED,
    )
    SqlLedgerWriter(db).append(record)
    row = db.get(SessionLedgerRow, "sess-seed")
    assert row is not None
    assert row.blueprint_source == "seed"
    assert row.blueprint_content_hash is None


def test_legacy_roundtrip_both_null(db):
    record = _build(db, "sess-legacy")
    assert record.blueprint_content_hash is None
    assert record.blueprint_source is None
    SqlLedgerWriter(db).append(record)
    row = db.get(SessionLedgerRow, "sess-legacy")
    assert row.blueprint_source is None
    assert row.blueprint_content_hash is None


def test_e1_transitional_hash_without_source_still_legal(db):
    record = _build(db, "sess-e1", blueprint_content_hash=HASH, blueprint_source=None)
    SqlLedgerWriter(db).append(record)
    row = db.get(SessionLedgerRow, "sess-e1")
    assert row.blueprint_content_hash == HASH
    assert row.blueprint_source is None


def test_illegal_seed_with_hash_rejected_at_writer():
    with pytest.raises(ValueError, match="BLUEPRINT_SOURCE_MISMATCH"):
        validate_blueprint_provenance(HASH, BLUEPRINT_SOURCE_SEED)


def test_illegal_published_without_hash_rejected_at_writer():
    with pytest.raises(ValueError, match="BLUEPRINT_SOURCE_MISMATCH"):
        validate_blueprint_provenance(None, BLUEPRINT_SOURCE_PUBLISHED)


def test_illegal_seed_with_hash_rejected_at_build_record(db):
    with pytest.raises(ValueError, match="BLUEPRINT_SOURCE_MISMATCH"):
        _build(
            db,
            "sess-bad",
            blueprint_content_hash=HASH,
            blueprint_source=BLUEPRINT_SOURCE_SEED,
        )


def test_db_check_rejects_seed_with_hash(db):
    row = SessionLedgerRow(
        session_id="sess-ck-bad",
        learner_pseudo_id="L1",
        cohort_id="cohortA",
        case_id="c1",
        case_version="1.0",
        physio_engine_version="physio@v1",
        rubric_version="rub1",
        replay_hash="sha256:x",
        blueprint_content_hash=HASH,
        blueprint_source="seed",
        finalized_at_utc=datetime.now(timezone.utc),
        confirmation="confirmed",
        grade_total=0.8,
        grade_passed=True,
        axis_normalized={},
        evidence=[],
        flags=[],
        actions=[],
    )
    db.add(row)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_idempotent_reappend_matching_source(db):
    record = _build(
        db,
        "sess-idem",
        blueprint_content_hash=HASH,
        blueprint_source=BLUEPRINT_SOURCE_PUBLISHED,
    )
    writer = SqlLedgerWriter(db)
    writer.append(record)
    writer.append(record)  # same fingerprint


def test_conflict_when_source_changes(db):
    legacy = _legacy()
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
        blueprint_content_hash=HASH,
        blueprint_source=BLUEPRINT_SOURCE_PUBLISHED,
    )
    SqlLedgerWriter(db).append(build_record(session_id="sess-conflict", **base))
    # Same session, source dropped to legacy null while keeping hash → fingerprint change
    with pytest.raises(LedgerConflictError):
        SqlLedgerWriter(db).append(
            build_record(
                session_id="sess-conflict",
                **{**base, "blueprint_source": None},
            )
        )


@pytest.mark.asyncio
async def test_ledger_read_exposes_blueprint_source(db):
    record = _build(
        db,
        "sess-view",
        blueprint_content_hash=HASH,
        blueprint_source=BLUEPRINT_SOURCE_PUBLISHED,
    )
    SqlLedgerWriter(db).append(record)
    # Enough cohort peers for k-anon on other endpoints; learner evidence does not need floor.
    svc = LedgerReadService(SqlLedgerReader(db))
    views = await svc.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="learner-42"),
        ConsentScope.SELF_LEARNER,
    )
    assert len(views) == 1
    assert views[0].blueprint_source == "published"
    assert views[0].blueprint_content_hash == HASH
