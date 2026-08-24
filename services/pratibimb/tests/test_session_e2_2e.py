"""E2.2.e: blueprint_source stamp, corpus envelope load, seed one-way flip."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.case_gen.envelope import case_blueprint_from_envelope_json
from services.pratibimb.app.case_gen.sampler import CaseSampler, EmptyCorpusError
from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit, Severity
from services.pratibimb.app.eval.scorer import Turn
from services.pratibimb.app.physio.state import PhysioTrace as LegacyPhysioTrace
from services.pratibimb.app.session.manager import SessionManager
from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_projector import (
    CorpusProjector,
    canonical_envelope_dumps,
)
from services.pratibimb.ledger.db import seed_trusted_physio_version
from services.pratibimb.ledger.models import Base, PublishedCaseCorpusRow, SessionLedgerRow
from services.pratibimb.ledger.writer import (
    BLUEPRINT_SOURCE_PUBLISHED,
    BLUEPRINT_SOURCE_SEED,
    SqlLedgerWriter,
    append_graded_session,
    build_record,
)
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


def _grade_bits():
    legacy = LegacyPhysioTrace()
    legacy.set_flag(
        "hypotension", 60.0, cause=FlagCause.DRUG_PD_EFFECT, detail={"drug": "morphine"}
    )
    legacy.clear_flag("hypotension", 180.0, cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS)
    legacy.add_event(80.0, "diagnosis", {"dx": "anaphylaxis", "confidence": 0.9})
    blueprint = GradingBlueprint(
        case_id=CASE_ID,
        case_version=VERSION,
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
    typed, _ = legacy.to_typed_trace(case_id=CASE_ID, case_version=VERSION)
    typed.record_diagnosis(DiagnosisEvent(dx="anaphylaxis", confidence=0.9, t_s=80.0))
    grade = Nirikshak(blueprint, typed).grade()
    turns = [
        Turn(speaker="learner", text="anaphylaxis", ts=80.0, action="dx:anaphylaxis"),
    ]
    return legacy, blueprint, grade, turns


def test_create_stamps_published_source_and_hash(authoring_session, corpus_session):
    _seed_and_project(authoring_session, corpus_session)
    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    resp = mgr.create_session(
        CreateSessionRequest(
            learner_id="L1",
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=VERSION,
        )
    )
    state = mgr._sessions[resp.session_id]
    assert state.blueprint_source == BLUEPRINT_SOURCE_PUBLISHED
    assert state.blueprint_content_hash == HASH
    assert state.blueprint_source != BLUEPRINT_SOURCE_SEED
    assert state.blueprint_source is not None


def test_project_on_miss_heal_also_stamps_published(
    authoring_session, corpus_session
):
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
    assert corpus_session.get(PublishedCaseCorpusRow, pub.id) is None

    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    resp = mgr.create_session(
        CreateSessionRequest(
            learner_id="L1",
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=VERSION,
        )
    )
    state = mgr._sessions[resp.session_id]
    assert state.blueprint_source == BLUEPRINT_SOURCE_PUBLISHED
    assert state.blueprint_content_hash == HASH


def test_envelope_loaded_byte_equal_to_corpus_row(authoring_session, corpus_session):
    row = _seed_and_project(authoring_session, corpus_session)
    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    resp = mgr.create_session(
        CreateSessionRequest(
            learner_id="L1",
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=VERSION,
        )
    )
    state = mgr._sessions[resp.session_id]
    assert state.corpus_envelope_json == row.envelope_json
    assert isinstance(state.corpus_envelope_json, str)
    assert state.case.case_id == CASE_ID


def test_corpus_hit_does_not_read_authoring_draft_for_envelope(
    authoring_session, corpus_session
):
    _seed_and_project(authoring_session, corpus_session)
    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    with patch.object(authoring_session, "get", wraps=authoring_session.get) as spy_get:
        resp = mgr.create_session(
            CreateSessionRequest(
                learner_id="L1",
                tenant_id=TENANT,
                case_id=CASE_ID,
                case_version=VERSION,
            )
        )
        # Warm path: get_by_triple + envelope deserialize — no CaseDraftRow fetch.
        draft_gets = [
            c
            for c in spy_get.call_args_list
            if c.args and c.args[0] is CaseDraftRow
        ]
        assert draft_gets == [], "envelope must come from corpus, not live draft"
    assert mgr._sessions[resp.session_id].corpus_envelope_json is not None


def test_finalize_writes_published_source(ledger_db, authoring_session, corpus_session):
    _seed_and_project(authoring_session, corpus_session)
    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    resp = mgr.create_session(
        CreateSessionRequest(
            learner_id="L1",
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=VERSION,
        )
    )
    state = mgr._sessions[resp.session_id]
    legacy, blueprint, grade, turns = _grade_bits()
    append_graded_session(
        ledger_db,
        session_id=resp.session_id,
        learner_pseudo_id="L1",
        cohort_id="cohortA",
        case_id=CASE_ID,
        case_version=VERSION,
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
    assert row.blueprint_source == "published"
    assert row.blueprint_content_hash == HASH


def test_seed_when_tenant_empty_allow_fallback(corpus_session):
    sampler = CaseSampler(corpus_session=corpus_session, allow_seed_fallback=True)
    sampled = sampler.sample_with_provenance(tenant_id=TENANT)
    assert sampled.blueprint_source == BLUEPRINT_SOURCE_SEED
    assert sampled.blueprint_content_hash is None
    assert sampled.corpus_envelope_json is None


def test_one_way_flip_seed_unreachable_after_projection(
    authoring_session, corpus_session
):
    """Seed fallback must not leak into a populated tenant (ALLOW_SEED_FALLBACK still on)."""
    sampler = CaseSampler(corpus_session=corpus_session, allow_seed_fallback=True)
    before = sampler.sample_with_provenance(tenant_id=TENANT)
    assert before.blueprint_source == BLUEPRINT_SOURCE_SEED

    _seed_and_project(authoring_session, corpus_session)

    after = sampler.sample_with_provenance(tenant_id=TENANT)
    assert after.blueprint_source == BLUEPRINT_SOURCE_PUBLISHED, (
        "seed fallback leaked into a populated tenant — one-way flip broken"
    )
    assert after.blueprint_content_hash == HASH
    assert after.corpus_envelope_json is not None
    # Structural: projected count > 0 makes seed branch unreachable.
    assert sampler.tenant_projected_count(TENANT) >= 1


def test_empty_corpus_seed_fallback_off_fails_closed(corpus_session):
    sampler = CaseSampler(corpus_session=corpus_session, allow_seed_fallback=False)
    with pytest.raises(EmptyCorpusError):
        sampler.sample_with_provenance(tenant_id=TENANT)


def test_legacy_no_corpus_session_stamps_hash_null_source(authoring_session):
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
