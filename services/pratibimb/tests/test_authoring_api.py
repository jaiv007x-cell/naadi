"""Authoring harness HTTP API — phase A endpoints."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.eval.rubric import Axis, Severity
from services.pratibimb.app.main import app
from services.pratibimb.authoring.constants import (
    RAMESH_CASE_ID,
    RAMESH_DRAFT_ID,
    RAMESH_TENANT_ID,
    CaseWorkflowState,
    FixtureKind,
)
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.deps import (
    authoring_db,
    reset_authoring_wiring_cache,
    seed_authoring_scope,
)
from services.pratibimb.authoring.seed import seed_ramesh_draft
from services.pratibimb.ledger_read.deps import (
    get_jwks_cache,
    reset_auth_wiring_cache,
    set_consent_store,
)
from services.pratibimb.ledger_read.consent_store import InMemoryConsentGrantStore
from services.pratibimb.tests.test_auth_jwt import AUD, ISS, JWKS_URL, TENANT, rsa_keypair
from shared.schemas.ledger_read import ConsentScope
from shared.schemas.trace import DrugAdminEvent, PhysioTrace

AUTHOR = "author-1"


def _blueprint_json(*, case_id: str = "C1", case_version: str = "1.0.0") -> dict:
    return {
        "identity": {
            "case_id": case_id,
            "version": case_version,
            "title": "Test case",
            "corpus_tier": "draft",
            "assessment_mode": "practice",
        },
        "targeting": {
            "roles": ["gnm"],
            "competencies": ["cardiac.stemi"],
            "difficulty": 0.5,
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
            "guideline_versions": [],
            "reviewed_at": None,
            "content_hash": "",
        },
        "schema_version": "2.0.0",
        "grading_blueprint": {
            "case_id": case_id,
            "case_version": case_version,
            "rubric_version": "0.3.0",
            "hits": [
                {
                    "id": "aspirin",
                    "axis": Axis.ACTION.value,
                    "matcher": "drug_given",
                    "params": {"drug_id": "aspirin"},
                    "points": 10.0,
                    "severity": Severity.MAJOR.value,
                    "required": True,
                    "fail_case_on_violation": True,
                }
            ],
        },
    }


def _pass_trace(case_id: str = "C1", case_version: str = "1.0.0") -> dict:
    trace = PhysioTrace(case_id=case_id, case_version=case_version)
    trace.record_drug(
        DrugAdminEvent(drug_id="aspirin", dose=325, dose_unit="mg", route="PO", t_s=60.0)
    )
    return trace.to_canonical()


def _miss_trace(case_id: str = "C1", case_version: str = "1.0.0") -> dict:
    return PhysioTrace(case_id=case_id, case_version=case_version).to_canonical()


def _dev_auth(sub: str, tenant: str = TENANT) -> dict[str, str]:
    return {"X-Dev-Subject": sub, "X-Dev-Tenant": tenant}


@pytest.fixture
def authoring_client(monkeypatch, tmp_path, rsa_keypair):
    db_url = f"sqlite:///{tmp_path / 'authoring_api.db'}"
    monkeypatch.setenv("AUTHORING_DATABASE_URL", db_url)
    monkeypatch.setenv("LEDGER_DATABASE_URL", f"sqlite:///{tmp_path / 'ledger_api.db'}")
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "1")
    monkeypatch.setenv("CONSENT_STORE", "in_memory")
    monkeypatch.setenv(
        "TENANT_TRUST_CONFIG",
        f"tenant={TENANT},iss={ISS},aud={AUD},jwks={JWKS_URL}",
    )
    reset_auth_wiring_cache()
    reset_authoring_wiring_cache()
    set_consent_store(InMemoryConsentGrantStore())
    get_jwks_cache().seed(JWKS_URL, [rsa_keypair[0]])
    asyncio.run(
        seed_authoring_scope(
            tenant_id=TENANT,
            subject_id=AUTHOR,
            scope=ConsentScope.AUTHORING_SUBMIT,
        )
    )

    # init_authoring_schema -> _sqlite_schema_shim attaches :memory: as `authoring`
    # so schema-qualified FKs resolve under SQLite like Postgres.
    eng = create_engine(db_url, future=True)
    init_authoring_schema(eng)
    SessionLocal = sessionmaker(bind=eng, expire_on_commit=False, future=True)

    def _override_authoring_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[authoring_db] = _override_authoring_db

    with SessionLocal() as session:
        seed_ramesh_draft(session)

    yield TestClient(app)

    app.dependency_overrides.clear()
    reset_auth_wiring_cache()
    reset_authoring_wiring_cache()


def _seed_fixtures(client: TestClient, draft_id: str) -> None:
    client.put(
        f"/v1/authoring/drafts/{draft_id}/fixtures",
        headers=_dev_auth(AUTHOR),
        json={
            "fixture_kind": FixtureKind.PERFECT_PATH.value,
            "trace_json": _pass_trace(),
            "expected_grade_json": {"passed": True},
        },
    ).raise_for_status()
    client.put(
        f"/v1/authoring/drafts/{draft_id}/fixtures",
        headers=_dev_auth(AUTHOR),
        json={
            "fixture_kind": FixtureKind.CRITICAL_MISS.value,
            "trace_json": _miss_trace(),
            "expected_grade_json": {"passed": False},
        },
    ).raise_for_status()


def test_create_draft_returns_content_hash(authoring_client):
    resp = authoring_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={
            "blueprint_json": _blueprint_json(),
            "blueprint_version": "1.0.0",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["current_state"] == CaseWorkflowState.DRAFT.value
    assert len(body["content_hash"]) == 64
    assert body["case_id"] == "C1"


def test_draft_content_hash_stable_after_orm_reload(authoring_client):
    """Create and GET must return the same canonical hash — no JSONB key-order drift."""
    created = authoring_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": _blueprint_json(), "blueprint_version": "1.0.0"},
    )
    assert created.status_code == 201
    create_body = created.json()
    draft_id = create_body["id"]
    create_hash = create_body["content_hash"]

    reloaded = authoring_client.get(
        f"/v1/authoring/drafts/{draft_id}",
        headers=_dev_auth(AUTHOR),
    )
    assert reloaded.status_code == 200
    assert reloaded.json()["content_hash"] == create_hash


def test_ramesh_seed_is_present(authoring_client):
    resp = authoring_client.get(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}",
        headers=_dev_auth("reviewer", tenant=RAMESH_TENANT_ID),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"] == RAMESH_CASE_ID
    assert body["current_state"] == CaseWorkflowState.DRAFT.value
    assert len(body["content_hash"]) == 64
    assert body["blueprint_json"]["identity"]["corpus_tier"] == "draft"
    assert body["blueprint_json"].get("grading_blueprint") is not None
    assert len(body["blueprint_json"]["grading_blueprint"]["hits"]) == 3


def test_submit_for_review_runs_dry_run(authoring_client):
    created = authoring_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": _blueprint_json(), "blueprint_version": "1.0.0"},
    ).json()
    draft_id = created["id"]
    _seed_fixtures(authoring_client, draft_id)

    resp = authoring_client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={"reason": "ready"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_state"] == CaseWorkflowState.IN_REVIEW.value
    assert len(body["transitions"]) == 1
    assert body["transitions"][0]["dry_run_result_hash"] is not None


def test_revert_in_review_to_draft(authoring_client):
    created = authoring_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": _blueprint_json(), "blueprint_version": "1.0.0"},
    ).json()
    draft_id = created["id"]
    _seed_fixtures(authoring_client, draft_id)
    authoring_client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={},
    ).raise_for_status()

    resp = authoring_client.post(
        f"/v1/authoring/drafts/{draft_id}/revert",
        headers=_dev_auth(AUTHOR),
        json={"reason": "needs edits"},
    )
    assert resp.status_code == 200
    assert resp.json()["current_state"] == CaseWorkflowState.DRAFT.value


def test_dry_run_endpoint_without_submit(authoring_client):
    created = authoring_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": _blueprint_json(), "blueprint_version": "1.0.0"},
    ).json()
    draft_id = created["id"]
    _seed_fixtures(authoring_client, draft_id)

    resp = authoring_client.post(
        f"/v1/authoring/drafts/{draft_id}/dry-run",
        headers=_dev_auth(AUTHOR),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is True
    assert len(body["result_hash"]) == 64


def test_submit_without_scope_is_denied(authoring_client):
    created = authoring_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": _blueprint_json(), "blueprint_version": "1.0.0"},
    ).json()
    draft_id = created["id"]
    _seed_fixtures(authoring_client, draft_id)

    resp = authoring_client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth("no-scope-user"),
        json={},
    )
    assert resp.status_code == 403


def test_update_draft_blocked_in_review(authoring_client):
    created = authoring_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": _blueprint_json(), "blueprint_version": "1.0.0"},
    ).json()
    draft_id = created["id"]
    _seed_fixtures(authoring_client, draft_id)
    authoring_client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={},
    ).raise_for_status()

    resp = authoring_client.put(
        f"/v1/authoring/drafts/{draft_id}",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": _blueprint_json(case_id="C2"), "blueprint_version": "1.0.1"},
    )
    assert resp.status_code == 409


def test_list_drafts_scoped_to_tenant(authoring_client):
    authoring_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR, tenant=TENANT),
        json={"blueprint_json": _blueprint_json(), "blueprint_version": "1.0.0"},
    ).raise_for_status()

    tenant_drafts = authoring_client.get(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR, tenant=TENANT),
    ).json()
    assert any(d["case_id"] == "C1" for d in tenant_drafts)

    other_tenant = authoring_client.get(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR, tenant="other-tenant"),
    ).json()
    assert all(d["case_id"] != "C1" for d in other_tenant)
