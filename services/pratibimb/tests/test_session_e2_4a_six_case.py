"""
E2.4.a: six-case provenance decision table (sampler + SessionManager create).

Single suite walking every branch — future readers should not reconstruct
behavior from scattered E2.2.e tests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.case_gen.sampler import (
    BLUEPRINT_SOURCE_PUBLISHED,
    BLUEPRINT_SOURCE_SEED,
    CaseSampler,
    EmptyCorpusError,
)
from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit, Severity
from services.pratibimb.app.eval.scorer import Turn
from services.pratibimb.app.physio.state import PhysioTrace as LegacyPhysioTrace
from services.pratibimb.app.session.manager import SessionManager
from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_projector import CorpusProjector
from services.pratibimb.ledger.db import seed_trusted_physio_version
from services.pratibimb.ledger.models import Base, PublishedCaseCorpusRow, SessionLedgerRow
from services.pratibimb.ledger.writer import append_graded_session
from shared.schemas.flag_cause import FlagCause
from shared.schemas.session import CreateSessionRequest
from shared.schemas.trace import DiagnosisEvent

TENANT = "tenant-a"
CASE_ID = "C1"
VERSION = "v1"
HASH = "a" * 64
GUIDELINE_PIN = "acc-2024@sha256:" + ("c" * 64)


def _v2_blueprint(*, case_id: str = CASE_ID, version: str = VERSION) -> dict:
    return {
        "identity": {
            "case_id": case_id,
            "version": version,
            "title": "Test case",
            "corpus_tier": "silver",
            "assessment_mode": "formative",
        },
        "targeting": {
            "roles": ["gnm"],
            "competencies": ["cardiac.stemi"],
            "difficulty": 0.55,
            "prerequisites": [],
        },
        "patient": {
            "demographics": {
                "name": "Test Patient",
                "age": 47,
                "sex": "M",
                "occupation": "driver",
                "state": "Maharashtra",
                "city": "Nagpur",
                "native_language": "mr",
                "education_years": 5,
                "monthly_income_inr": 14000,
            },
            "language": "mr",
            "voice_profile": "default",
            "literacy_years": 5,
            "persona_notes": "",
        },
        "clinical_truth": {
            "primary_diagnosis": "Inferior wall STEMI",
            "icd10": "I21.19",
            "differentials": [],
            "symptoms_present": ["chest pain"],
            "findings_present": [],
            "absent_findings": [],
            "allergies": [],
            "medications": [],
            "onset_minutes_ago": 40,
        },
        "environment": {
            "setting": "opd",
            "resources": ["ecg"],
            "distractors": [],
            "difficulty_parameters": {"time_pressure_seconds": 900},
        },
        "physiology": {
            "engine_version": "0.2.0",
            "initial_vitals": {
                "hr": 104,
                "sbp": 148,
                "dbp": 92,
                "spo2": 96,
                "rr": 20,
                "temp_c": 36.9,
            },
            "deterioration_rules": [],
            "pharmacology_profile": "default",
        },
        "interaction": {
            "allowed_disclosures": ["chest pain"],
            "hidden_facts": [],
            "family_personas": [],
            "dialogue_constraints": [],
        },
        "provenance": {
            "author": "unknown",
            "clinical_reviewer": None,
            "sources": [],
            "guideline_versions": [GUIDELINE_PIN],
            "reviewed_at": None,
            "content_hash": "",
        },
        "schema_version": "2.0.0",
        "grading_blueprint": None,
    }


@pytest.fixture
def authoring_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_authoring_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def corpus_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def ledger_db(monkeypatch):
    monkeypatch.delenv("LEDGER_DISABLED", raising=False)
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    seed_trusted_physio_version(engine, approved_by="test")
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        yield session


def _seed_and_project(authoring_session, corpus_session) -> PublishedCaseCorpusRow:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=TENANT,
        author_subject_id="author-1",
        blueprint_json=_v2_blueprint(),
        blueprint_version=VERSION,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        current_state="PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    authoring_session.add(draft)
    authoring_session.flush()
    pub = PublishedCaseVersionRow(
        id=str(uuid.uuid4()),
        draft_id=draft.id,
        tenant_id=TENANT,
        case_id=CASE_ID,
        version=VERSION,
        assessment_mode="formative",
        content_hash=HASH,
        harness_version=HARNESS_VERSION,
        published_at=datetime.now(timezone.utc),
        published_by="publisher-5",
    )
    authoring_session.add(pub)
    authoring_session.commit()
    row = CorpusProjector(authoring_session, corpus_session).project_one(pub.id)
    corpus_session.commit()
    return row


def _insert_probe_only_corpus_row(corpus_session) -> None:
    """Projected (counts toward flip) but not sample-eligible."""
    now = datetime.now(timezone.utc)
    corpus_session.add(
        PublishedCaseCorpusRow(
            id=str(uuid.uuid4()),
            tenant_id=TENANT,
            case_id="RETIRED-C",
            version="v1",
            content_hash=HASH,
            assessment_mode="formative",
            workflow_state="RETIRED",
            probe_only=True,
            harness_version=HARNESS_VERSION,
            published_at=now,
            envelope_json="{}",
            projected_at=now,
        )
    )
    corpus_session.commit()


def _grade_bits(*, case_id: str = CASE_ID, version: str = VERSION):
    legacy = LegacyPhysioTrace()
    legacy.set_flag(
        "hypotension", 60.0, cause=FlagCause.DRUG_PD_EFFECT, detail={"drug": "morphine"}
    )
    legacy.clear_flag("hypotension", 180.0, cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS)
    legacy.add_event(80.0, "diagnosis", {"dx": "anaphylaxis", "confidence": 0.9})
    blueprint = GradingBlueprint(
        case_id=case_id,
        case_version=version,
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
    typed, _ = legacy.to_typed_trace(case_id=case_id, case_version=version)
    typed.record_diagnosis(DiagnosisEvent(dx="anaphylaxis", confidence=0.9, t_s=80.0))
    grade = Nirikshak(blueprint, typed).grade()
    turns = [
        Turn(speaker="learner", text="anaphylaxis", ts=80.0, action="dx:anaphylaxis"),
    ]
    return legacy, blueprint, grade, turns


# --- six-case matrix ---


def test_case1_projected_random_create_stamps_published(
    authoring_session, corpus_session
):
    row = _seed_and_project(authoring_session, corpus_session)
    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        allow_seed_fallback=True,
    )
    resp = mgr.create_session(
        CreateSessionRequest(learner_id="L1", tenant_id=TENANT)
    )
    state = mgr._sessions[resp.session_id]
    assert state.blueprint_source == BLUEPRINT_SOURCE_PUBLISHED
    assert state.blueprint_content_hash == HASH
    assert state.corpus_envelope_json == row.envelope_json
    assert state.blueprint_source != BLUEPRINT_SOURCE_SEED


def test_case2_seed_create_finalize_ledger_ie22_4(
    ledger_db, corpus_session, authoring_session
):
    """I-E24-1 / I-E22-4: seed ⇒ hash NULL through create → append → ledger."""
    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        allow_seed_fallback=True,
    )
    resp = mgr.create_session(
        CreateSessionRequest(learner_id="L1", tenant_id=TENANT)
    )
    state = mgr._sessions[resp.session_id]
    assert state.blueprint_source == BLUEPRINT_SOURCE_SEED
    assert state.blueprint_content_hash is None
    assert state.corpus_envelope_json is None
    # Seed must not write into corpus.
    from sqlalchemy import func, select

    n = corpus_session.scalar(
        select(func.count()).select_from(PublishedCaseCorpusRow)
    )
    assert int(n or 0) == 0

    legacy, blueprint, grade, turns = _grade_bits(
        case_id=state.case.case_id, version="seed"
    )
    append_graded_session(
        ledger_db,
        session_id=resp.session_id,
        learner_pseudo_id="L1",
        cohort_id="cohortA",
        case_id=state.case.case_id,
        case_version="seed",
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash=state.blueprint_content_hash,
        blueprint_source=state.blueprint_source,
        tenant_id=TENANT,
        authoring_session=authoring_session,
    )
    row = ledger_db.get(SessionLedgerRow, resp.session_id)
    assert row is not None
    assert row.blueprint_source == "seed"
    assert row.blueprint_content_hash is None
    # Distinguishes from pre-E2.2 legacy (NULL, NULL).
    assert row.blueprint_source is not None


def test_case3_empty_corpus_fallback_off_empty_corpus_error(corpus_session):
    mgr = SessionManager(
        corpus_session=corpus_session,
        allow_seed_fallback=False,
    )
    with pytest.raises(EmptyCorpusError):
        mgr.create_session(CreateSessionRequest(learner_id="L1", tenant_id=TENANT))
    assert mgr._sessions == {}


def test_case4_only_probe_only_rows_same_empty_corpus_error(corpus_session):
    """
    Flip threshold crossed (projected ≥1) but sample pool empty — same
    EmptyCorpusError as case 3 (I-E24-5). Not a seed leak.
    """
    _insert_probe_only_corpus_row(corpus_session)
    sampler = CaseSampler(corpus_session=corpus_session, allow_seed_fallback=True)
    assert sampler.tenant_projected_count(TENANT) >= 1

    mgr = SessionManager(
        corpus_session=corpus_session,
        allow_seed_fallback=True,  # still on — must not leak seed
    )
    with pytest.raises(EmptyCorpusError):
        mgr.create_session(CreateSessionRequest(learner_id="L1", tenant_id=TENANT))
    assert mgr._sessions == {}


def test_case5_explicit_pick_warm_no_seed_path(authoring_session, corpus_session):
    row = _seed_and_project(authoring_session, corpus_session)
    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        allow_seed_fallback=True,
    )
    with patch.object(
        mgr._sampler, "_sample_seed", wraps=mgr._sampler._sample_seed
    ) as seed_spy:
        resp = mgr.create_session(
            CreateSessionRequest(
                learner_id="L1",
                tenant_id=TENANT,
                case_id=CASE_ID,
                case_version=VERSION,
            )
        )
        assert seed_spy.call_count == 0, "warm pick must not touch seed path"
    state = mgr._sessions[resp.session_id]
    assert state.blueprint_source == BLUEPRINT_SOURCE_PUBLISHED
    assert state.blueprint_content_hash == HASH
    assert state.corpus_envelope_json == row.envelope_json


def test_case6_legacy_hatch_hash_null_source(authoring_session):
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=TENANT,
        author_subject_id="author-1",
        blueprint_json=_v2_blueprint(),
        blueprint_version=VERSION,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        current_state="PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    authoring_session.add(draft)
    authoring_session.flush()
    authoring_session.add(
        PublishedCaseVersionRow(
            id=str(uuid.uuid4()),
            draft_id=draft.id,
            tenant_id=TENANT,
            case_id=CASE_ID,
            version=VERSION,
            assessment_mode="formative",
            content_hash=HASH,
            harness_version=HARNESS_VERSION,
            published_at=datetime.now(timezone.utc),
            published_by="publisher-5",
        )
    )
    authoring_session.commit()

    mgr = SessionManager(authoring_session=authoring_session)  # no corpus_session
    resp = mgr.create_session(
        CreateSessionRequest(
            learner_id="L1",
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=VERSION,
        )
    )
    state = mgr._sessions[resp.session_id]
    assert state.blueprint_content_hash == HASH
    assert state.blueprint_source is None
    assert state.corpus_envelope_json is None
