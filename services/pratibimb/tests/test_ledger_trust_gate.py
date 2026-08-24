"""Evidence Ledger v1 trust-gate policy tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.beema.ledger.interface import ConfirmationState
from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit
from services.pratibimb.app.eval.scorer import Turn
from services.pratibimb.app.physio.state import PhysioTrace as LegacyPhysioTrace
from services.pratibimb.app.physio.version_probe import get_version
from services.pratibimb.ledger.db import (
    init_ledger_schema,
    promote_physio_version,
)
from services.pratibimb.ledger.models import Base, TrustedPhysioVersion
from services.pratibimb.ledger.writer import (
    SqlLedgerWriter,
    UntrustedPhysioError,
    build_record,
)
from shared.schemas.trace import DiagnosisEvent


def _production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PRATIBIMB_ENV", raising=False)


def _minimal_record(physio_version: str):
    legacy = LegacyPhysioTrace()
    legacy.add_event(70.0, "diagnosis", {"dx": "anaphylaxis", "confidence": 0.9})
    blueprint = GradingBlueprint(
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
        ),
    )
    typed, _ = legacy.to_typed_trace(case_id=blueprint.case_id, case_version=blueprint.case_version)
    typed.record_diagnosis(DiagnosisEvent(dx="anaphylaxis", confidence=0.9, t_s=70.0))
    grade = Nirikshak(blueprint, typed).grade()
    grade = replace(grade, physio_version=physio_version)
    turns = [Turn(speaker="learner", text="anaphylaxis", ts=70.0, action="dx:anaphylaxis")]
    return build_record(
        session_id="sess-trust-001",
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


def test_production_boot_does_not_auto_trust_unknown_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    _production_env(monkeypatch)
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", future=True)
    init_ledger_schema(engine)

    version = get_version()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        assert session.get(TrustedPhysioVersion, version) is None


def test_unknown_version_rejected_until_explicitly_promoted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    _production_env(monkeypatch)
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", future=True)
    Base.metadata.create_all(engine)

    version = get_version()
    record = _minimal_record(version)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        with pytest.raises(UntrustedPhysioError, match="not in trusted registry"):
            SqlLedgerWriter(session).append(record)

    promote_physio_version(
        engine,
        version,
        approved_by="clinical_ops",
        notes="Passed replay suite 2026-08-19",
    )

    with SessionLocal() as session:
        assert session.get(TrustedPhysioVersion, version) is not None
        replay_hash = SqlLedgerWriter(session).append(record)
        assert replay_hash == record.replay_hash
