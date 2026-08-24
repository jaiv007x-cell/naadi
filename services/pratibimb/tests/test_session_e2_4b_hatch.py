"""E2.4.b: legacy hatch hygiene — ledger_legacy_hatch_total{tenant}."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.session.manager import SessionManager
from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.metrics import (
    LEDGER_LEGACY_HATCH_TOTAL,
    reset_authoring_metrics_for_tests,
)
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from shared.schemas.session import CreateSessionRequest

TENANT = "tenant-hatch"
CASE_ID = "C1"
VERSION = "v1"
HASH = "d" * 64
GUIDELINE_PIN = "acc-2024@sha256:" + ("c" * 64)


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_authoring_metrics_for_tests()
    yield
    reset_authoring_metrics_for_tests()


@pytest.fixture
def authoring_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_authoring_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield session
    session.close()


def _v2_blueprint() -> dict:
    return {
        "identity": {
            "case_id": CASE_ID,
            "version": VERSION,
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


def _metric_value(counter, **labels) -> float:
    key = tuple(sorted(labels.items()))
    with counter._lock:
        return float(counter._values.get(key, 0.0))


def test_legacy_hatch_increments_ledger_legacy_hatch_total(authoring_session):
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

    assert _metric_value(LEDGER_LEGACY_HATCH_TOTAL, tenant=TENANT) == 0.0

    mgr = SessionManager(authoring_session=authoring_session)  # no corpus_session
    mgr.create_session(
        CreateSessionRequest(
            learner_id="L1",
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=VERSION,
        )
    )
    assert _metric_value(LEDGER_LEGACY_HATCH_TOTAL, tenant=TENANT) == 1.0

    mgr.create_session(
        CreateSessionRequest(
            learner_id="L2",
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=VERSION,
        )
    )
    assert _metric_value(LEDGER_LEGACY_HATCH_TOTAL, tenant=TENANT) == 2.0
