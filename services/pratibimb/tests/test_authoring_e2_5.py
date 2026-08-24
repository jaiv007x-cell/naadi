"""E2.5: corpus-backed ``load_probe_case`` (I-E25-1…8)."""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.case_gen.envelope import case_blueprint_from_envelope_json
from services.pratibimb.app.case_gen.sampler import (
    CaseSampler,
    EmptyCorpusError,
    ProbeCaseNotFoundError,
    load_probe_case,
)
from services.pratibimb.app.physio.version_probe import compute_manifest
from services.pratibimb.audit.models_audit import LedgerReadAuditRow
from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_projector import CorpusProjector, canonical_envelope_dumps
from services.pratibimb.ledger.models import Base, PublishedCaseCorpusRow

TENANT = "tenant-a"
PROBE_CASE_ID = "probe.stemi.inferior.v1"
VERSION = "v1"
HASH = "a" * 64
GUIDELINE_PIN = "acc-2024@sha256:" + ("c" * 64)


def _v2_blueprint(*, case_id: str = PROBE_CASE_ID, version: str = VERSION) -> dict:
    return {
        "identity": {
            "case_id": case_id,
            "version": version,
            "title": "Probe case",
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
                "name": "Probe Patient",
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
def audit_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    import services.pratibimb.audit.models_audit  # noqa: F401

    LedgerReadAuditRow.__table__.create(engine, checkfirst=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield session
    session.close()


def _project_retired_probe(
    authoring_session,
    corpus_session,
    *,
    case_id: str = PROBE_CASE_ID,
) -> PublishedCaseCorpusRow:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=TENANT,
        author_subject_id="author-1",
        blueprint_json=_v2_blueprint(case_id=case_id),
        blueprint_version=VERSION,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        current_state="PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    authoring_session.add(draft)
    authoring_session.flush()
    now = datetime.now(timezone.utc)
    pub = PublishedCaseVersionRow(
        id=str(uuid.uuid4()),
        draft_id=draft.id,
        tenant_id=TENANT,
        case_id=case_id,
        version=VERSION,
        assessment_mode="formative",
        content_hash=HASH,
        harness_version=HARNESS_VERSION,
        published_at=now,
        published_by="publisher-5",
        retired_at=now,
        retired_by="retirer-6",
        retired_reason_code="policy_change",
    )
    authoring_session.add(pub)
    authoring_session.commit()
    row = CorpusProjector(authoring_session, corpus_session).project_one(pub.id)
    corpus_session.commit()
    assert row.probe_only is True
    return row


def _insert_probe_only_row(corpus_session, *, case_id: str = PROBE_CASE_ID) -> None:
    """Probe-only projected row — counts toward flip, not sample-eligible."""
    envelope = canonical_envelope_dumps(
        {
            "blueprint": _v2_blueprint(case_id=case_id),
            "fixtures": {},
            "harness_version": HARNESS_VERSION,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "workflow_state": "RETIRED",
        }
    )
    now = datetime.now(timezone.utc)
    corpus_session.add(
        PublishedCaseCorpusRow(
            id=str(uuid.uuid4()),
            tenant_id=TENANT,
            case_id=case_id,
            version=VERSION,
            content_hash=HASH,
            assessment_mode="formative",
            workflow_state="RETIRED",
            probe_only=True,
            harness_version=HARNESS_VERSION,
            published_at=now,
            envelope_json=envelope,
            projected_at=now,
        )
    )
    corpus_session.commit()


def test_1_corpus_hit_matches_envelope(authoring_session, corpus_session):
    """I-E25-1, I-E25-8: retire → project → load returns corpus envelope blueprint."""
    row = _project_retired_probe(authoring_session, corpus_session)
    expected = case_blueprint_from_envelope_json(row.envelope_json)
    loaded = load_probe_case(
        PROBE_CASE_ID,
        tenant_id=TENANT,
        corpus_session=corpus_session,
    )
    assert loaded.case_id == expected.case_id
    assert loaded.difficulty == expected.difficulty
    assert loaded.demographics.state == expected.demographics.state


def test_2_probe_only_tenant_sample_fails_probe_succeeds(corpus_session):
    """I-E25-4 + E2.4 case #4: flip crossed; sample empty; probe load ok."""
    _insert_probe_only_row(corpus_session)
    sampler = CaseSampler(corpus_session=corpus_session, allow_seed_fallback=True)
    with pytest.raises(EmptyCorpusError, match="all probe_only"):
        sampler.sample_with_provenance(tenant_id=TENANT)
    loaded = load_probe_case(
        PROBE_CASE_ID,
        tenant_id=TENANT,
        corpus_session=corpus_session,
    )
    assert loaded.case_id == PROBE_CASE_ID


def test_3_post_flip_miss_seed_not_consulted(authoring_session, corpus_session):
    """I-E25-5: projected rows exist, probe id missing — seed spy uncalled."""
    _project_retired_probe(authoring_session, corpus_session, case_id="other-probe")
    with patch(
        "services.pratibimb.app.case_gen.sampler._load_seed_probe_case"
    ) as seed_spy:
        with pytest.raises(ProbeCaseNotFoundError):
            load_probe_case(
                PROBE_CASE_ID,
                tenant_id=TENANT,
                corpus_session=corpus_session,
            )
        seed_spy.assert_not_called()


def test_4_pre_flip_seed_fallback(corpus_session):
    """I-E25-4 zero-row branch: seed probe when fallback allowed."""
    loaded = load_probe_case(
        PROBE_CASE_ID,
        tenant_id=TENANT,
        corpus_session=corpus_session,
        allow_seed_fallback=True,
    )
    assert loaded.case_id == PROBE_CASE_ID


def test_5_pre_flip_fail_closed(corpus_session):
    """I-E25-5 zero-row branch: no fallback → error."""
    with pytest.raises(ProbeCaseNotFoundError, match="seed fallback disabled"):
        load_probe_case(
            PROBE_CASE_ID,
            tenant_id=TENANT,
            corpus_session=corpus_session,
            allow_seed_fallback=False,
        )


def test_6_harness_path_unchanged():
    """I-E25-7: harness=True seed path; version_probe compat."""
    loaded = load_probe_case(PROBE_CASE_ID, harness=True)
    assert loaded.case_id == PROBE_CASE_ID
    manifest = compute_manifest()
    assert manifest.behavior_hash


def test_7_corpus_raise_does_not_fall_through_to_seed(corpus_session):
    """I-E25-2: corpus exception propagates; seed loader not invoked."""
    _insert_probe_only_row(corpus_session)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated corpus failure")

    with patch(
        "services.pratibimb.app.case_gen.sampler._load_corpus_probe_case",
        side_effect=_boom,
    ):
        with patch(
            "services.pratibimb.app.case_gen.sampler._load_seed_probe_case"
        ) as seed_spy:
            with pytest.raises(RuntimeError, match="simulated corpus failure"):
                load_probe_case(
                    PROBE_CASE_ID,
                    tenant_id=TENANT,
                    corpus_session=corpus_session,
                )
            seed_spy.assert_not_called()


def test_8_no_probe_only_parameter():
    """I-E25-3: signature inspection — no probe_only arg (E2.1 Pin 1 pattern)."""
    sig = inspect.signature(load_probe_case)
    assert "probe_only" not in sig.parameters


def test_9_no_ledger_read_audit_rows_after_probe_loads(
    corpus_session,
    audit_session,
):
    """I-E25-6: table-level — N probe loads leave ledger_read_audit row count at 0."""
    _insert_probe_only_row(corpus_session)
    before = audit_session.scalar(select(func.count()).select_from(LedgerReadAuditRow))
    for _ in range(5):
        load_probe_case(
            PROBE_CASE_ID,
            tenant_id=TENANT,
            corpus_session=corpus_session,
        )
    after = audit_session.scalar(select(func.count()).select_from(LedgerReadAuditRow))
    assert before == 0
    assert after == 0


def test_runtime_none_tenant_without_harness_raises():
    """I-E25-7: production None tenant fails loudly."""
    with pytest.raises(ValueError, match="harness=True"):
        load_probe_case(PROBE_CASE_ID)
