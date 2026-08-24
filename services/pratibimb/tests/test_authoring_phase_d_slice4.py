"""Phase D slice 4: read surface, draft lifecycle enrichment, metrics."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
]

from services.pratibimb.app.eval.rubric import Axis, Severity
from services.pratibimb.app.main import app
from services.pratibimb.audit.metrics import render_prometheus_metrics
from services.pratibimb.authoring.constants import (
    CaseWorkflowState,
    FixtureKind,
    RetireReasonCode,
    SCOPE_APPROVE,
)
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.deps import (
    authoring_db,
    reset_authoring_wiring_cache,
    seed_authoring_scope,
)
from services.pratibimb.authoring.metrics import (
    render_authoring_prometheus_metrics,
    reset_authoring_metrics_for_tests,
)
from services.pratibimb.authoring.state_machine import TransitionActor
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.ledger_read.consent_store import InMemoryConsentGrantStore
from services.pratibimb.ledger_read.deps import (
    get_jwks_cache,
    reset_auth_wiring_cache,
    set_consent_store,
)
from services.pratibimb.ledger_read.query_kinds import KNOWN_QUERY_KINDS
from services.pratibimb.tests.test_auth_jwt import AUD, ISS, JWKS_URL, TENANT, rsa_keypair
from services.pratibimb.tests.test_authoring_phase_a import (
    AUTHOR,
    REVIEWER,
    TENANT as STORE_TENANT,
    _miss_trace,
    _pass_trace,
)
from services.pratibimb.tests.test_authoring_phase_c import _submit
from shared.schemas.ledger_read import ConsentScope

PUBLISHER = "publisher-5"
RETIRER = "retirer-6"
CATALOG_READER = "catalog-reader-7"
OTHER_TENANT = "tenant-b"
GUIDELINE_PIN = "acc-2024@sha256:" + ("a" * 64)


def _full_blueprint(*, case_id: str = "C1", version: str = "1.0.0"):
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
            "guideline_versions": [GUIDELINE_PIN],
            "reviewed_at": None,
            "content_hash": "",
        },
        "schema_version": "2.0.0",
        "grading_blueprint": {
            "case_id": case_id,
            "case_version": version,
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


def _seed_publish_retire(store: CaseDraftStore, *, version: str = "1.0.0"):
    draft = store.create_draft(
        tenant_id=STORE_TENANT,
        author_subject_id=AUTHOR,
        blueprint_json=_full_blueprint(version=version),
        blueprint_version=version,
    )
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.PERFECT_PATH,
        trace_json=_pass_trace(),
        expected_grade_json={"passed": True},
    )
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.CRITICAL_MISS,
        trace_json=_miss_trace(),
        expected_grade_json={"passed": False},
    )
    _submit(store, draft.id)
    store.transition(
        draft.id,
        to_state=CaseWorkflowState.APPROVED,
        actor=TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE})),
    )
    store.publish_draft(
        draft.id,
        actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
    )
    store.retire_published(
        draft.id,
        actor=TransitionActor(subject_id=RETIRER, scopes=frozenset({SCOPE_APPROVE})),
        reason_code=RetireReasonCode.POLICY_CHANGE.value,
        reason_note="policy update",
    )
    return draft


def test_query_kind_placeholders_registered():
    assert "get_published_case_versions" in KNOWN_QUERY_KINDS
    assert "get_retirement_history" in KNOWN_QUERY_KINDS


def test_get_published_version_returns_retired_row_200(store, authoring_session):
    draft = _seed_publish_retire(store)
    authoring_session.commit()
    row = store.get_published_version(
        tenant_id=STORE_TENANT,
        case_id="C1",
        version="1.0.0",
    )
    assert row is not None
    assert row.retired_at is not None
    assert row.retired_reason_code == RetireReasonCode.POLICY_CHANGE.value


def test_list_published_include_retired_filter(store, authoring_session):
    _seed_publish_retire(store, version="1.0.0")
    draft2 = store.create_draft(
        tenant_id=STORE_TENANT,
        author_subject_id=AUTHOR,
        blueprint_json=_full_blueprint(version="2.0.0"),
        blueprint_version="2.0.0",
    )
    store.upsert_fixture(
        draft2.id,
        fixture_kind=FixtureKind.PERFECT_PATH,
        trace_json=_pass_trace(case_version="2.0.0"),
        expected_grade_json={"passed": True},
    )
    store.upsert_fixture(
        draft2.id,
        fixture_kind=FixtureKind.CRITICAL_MISS,
        trace_json=_miss_trace(case_version="2.0.0"),
        expected_grade_json={"passed": False},
    )
    _submit(store, draft2.id)
    store.transition(
        draft2.id,
        to_state=CaseWorkflowState.APPROVED,
        actor=TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE})),
    )
    store.publish_draft(
        draft2.id,
        actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
    )
    authoring_session.commit()

    all_rows = store.list_published_for_tenant_case(
        tenant_id=STORE_TENANT,
        case_id="C1",
        include_retired=True,
    )
    active_only = store.list_published_for_tenant_case(
        tenant_id=STORE_TENANT,
        case_id="C1",
        include_retired=False,
    )
    assert len(all_rows) == 2
    assert len(active_only) == 1
    assert active_only[0].version == "2.0.0"


def test_draft_detail_includes_lifecycle_timeline(store, authoring_session):
    draft = _seed_publish_retire(store)
    authoring_session.commit()
    transitions = store.list_transitions(draft.id)
    assert [t.to_state for t in transitions] == [
        CaseWorkflowState.IN_REVIEW.value,
        CaseWorkflowState.APPROVED.value,
        CaseWorkflowState.PUBLISHED.value,
        CaseWorkflowState.RETIRED.value,
    ]
    times = [t.occurred_at for t in transitions]
    assert times == sorted(times)
    pub = store.get_published_for_draft(draft.id)
    retired = store.list_retired_for_draft(draft.id)
    assert pub is not None
    assert len(retired) == 1


def test_metrics_increment_on_publish_and_retire(store, authoring_session):
    reset_authoring_metrics_for_tests()
    draft = store.create_draft(
        tenant_id=STORE_TENANT,
        author_subject_id=AUTHOR,
        blueprint_json=_full_blueprint(version="9.0.0"),
        blueprint_version="9.0.0",
    )
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.PERFECT_PATH,
        trace_json=_pass_trace(case_version="9.0.0"),
        expected_grade_json={"passed": True},
    )
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.CRITICAL_MISS,
        trace_json=_miss_trace(case_version="9.0.0"),
        expected_grade_json={"passed": False},
    )
    _submit(store, draft.id)
    store.transition(
        draft.id,
        to_state=CaseWorkflowState.APPROVED,
        actor=TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE})),
    )
    store.publish_draft(
        draft.id,
        actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
    )
    store.retire_published(
        draft.id,
        actor=TransitionActor(subject_id=RETIRER, scopes=frozenset({SCOPE_APPROVE})),
        reason_code=RetireReasonCode.GUIDELINE_CHANGE.value,
    )
    authoring_session.commit()

    body = render_authoring_prometheus_metrics()
    assert f'authoring_publish_total{{tenant="{STORE_TENANT}",tier="silver"}} 1' in body
    assert (
        f'authoring_retire_total{{reason_code="guideline_change",tenant="{STORE_TENANT}"}} 1'
        in body
    )
    assert f'authoring_published_active_total{{tenant="{STORE_TENANT}"}} 0' in body
    assert f'authoring_retired_total{{tenant="{STORE_TENANT}"}} 1' in body


def _dev_auth(sub: str, tenant: str = TENANT) -> dict[str, str]:
    return {"X-Dev-Subject": sub, "X-Dev-Tenant": tenant}


@pytest.fixture
def slice4_client(monkeypatch, tmp_path, rsa_keypair):
    db_url = f"sqlite:///{tmp_path / 'authoring_slice4.db'}"
    monkeypatch.setenv("AUTHORING_DATABASE_URL", db_url)
    monkeypatch.setenv("LEDGER_DATABASE_URL", f"sqlite:///{tmp_path / 'ledger_slice4.db'}")
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "1")
    monkeypatch.setenv("CONSENT_STORE", "in_memory")
    monkeypatch.setenv(
        "TENANT_TRUST_CONFIG",
        f"tenant={TENANT},iss={ISS},aud={AUD},jwks={JWKS_URL}",
    )
    reset_auth_wiring_cache()
    reset_authoring_wiring_cache()
    reset_authoring_metrics_for_tests()
    consent = InMemoryConsentGrantStore()
    set_consent_store(consent)
    get_jwks_cache().seed(JWKS_URL, [rsa_keypair[0]])

    for subject, scope in (
        (AUTHOR, ConsentScope.AUTHORING_SUBMIT),
        (REVIEWER, ConsentScope.AUTHORING_APPROVE),
        (PUBLISHER, ConsentScope.AUTHORING_APPROVE),
        (RETIRER, ConsentScope.AUTHORING_APPROVE),
        (CATALOG_READER, ConsentScope.AUTHORING_READ_PUBLISHED),
    ):
        asyncio.run(
            seed_authoring_scope(
                tenant_id=TENANT,
                subject_id=subject,
                scope=scope,
                store=consent,
            )
        )
    asyncio.run(
        seed_authoring_scope(
            tenant_id=OTHER_TENANT,
            subject_id=CATALOG_READER,
            scope=ConsentScope.AUTHORING_READ_PUBLISHED,
            store=consent,
        )
    )

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
    yield TestClient(app)
    app.dependency_overrides.clear()
    reset_auth_wiring_cache()
    reset_authoring_wiring_cache()


def _http_publish_retire(client: TestClient, *, version: str = "1.0.0") -> str:
    bp = _full_blueprint(version=version)
    created = client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": bp, "blueprint_version": version},
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]
    for kind, passed, trace in (
        (FixtureKind.PERFECT_PATH, True, _pass_trace(case_version=version)),
        (FixtureKind.CRITICAL_MISS, False, _miss_trace(case_version=version)),
    ):
        client.put(
            f"/v1/authoring/drafts/{draft_id}/fixtures",
            headers=_dev_auth(AUTHOR),
            json={
                "fixture_kind": kind.value,
                "trace_json": trace,
                "expected_grade_json": {"passed": passed},
            },
        ).raise_for_status()
    client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={},
    ).raise_for_status()
    client.post(
        f"/v1/authoring/drafts/{draft_id}/approve",
        headers=_dev_auth(REVIEWER),
        json={},
    ).raise_for_status()
    client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    ).raise_for_status()
    client.post(
        f"/v1/authoring/drafts/{draft_id}/retire",
        headers=_dev_auth(RETIRER),
        json={"reason_code": RetireReasonCode.POLICY_CHANGE.value, "reason_note": "policy"},
    ).raise_for_status()
    return draft_id


def test_http_get_published_retired_returns_200(slice4_client):
    draft_id = _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions/C1/1.0.0",
        headers=_dev_auth(CATALOG_READER),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retired_at"] is not None
    assert body["retired_reason_code"] == RetireReasonCode.POLICY_CHANGE.value
    assert body["retired_reason_note"] == "policy"


def test_http_list_published_include_retired(slice4_client):
    _http_publish_retire(slice4_client, version="1.0.0")
    # Second version: publish only (stay active)
    bp = _full_blueprint(version="2.0.0")
    created = slice4_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": bp, "blueprint_version": "2.0.0"},
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]
    for kind, passed, trace in (
        (FixtureKind.PERFECT_PATH, True, _pass_trace(case_version="2.0.0")),
        (FixtureKind.CRITICAL_MISS, False, _miss_trace(case_version="2.0.0")),
    ):
        slice4_client.put(
            f"/v1/authoring/drafts/{draft_id}/fixtures",
            headers=_dev_auth(AUTHOR),
            json={
                "fixture_kind": kind.value,
                "trace_json": trace,
                "expected_grade_json": {"passed": passed},
            },
        ).raise_for_status()
    slice4_client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={},
    ).raise_for_status()
    slice4_client.post(
        f"/v1/authoring/drafts/{draft_id}/approve",
        headers=_dev_auth(REVIEWER),
        json={},
    ).raise_for_status()
    slice4_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    ).raise_for_status()

    all_resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1&include_retired=true",
        headers=_dev_auth(CATALOG_READER),
    )
    active_resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1&include_retired=false",
        headers=_dev_auth(CATALOG_READER),
    )
    assert all_resp.status_code == 200
    assert active_resp.status_code == 200
    assert len(all_resp.json()["items"]) == 2
    assert len(active_resp.json()["items"]) == 1
    assert active_resp.json()["items"][0]["version"] == "2.0.0"
    assert active_resp.json()["items"][0]["retired_at"] is None


def test_http_draft_detail_lifecycle_timeline(slice4_client):
    draft_id = _http_publish_retire(slice4_client)
    detail = slice4_client.get(
        f"/v1/authoring/drafts/{draft_id}",
        headers=_dev_auth(AUTHOR),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["current_state"] == CaseWorkflowState.RETIRED.value
    assert body["published"]["retired_reason_code"] == RetireReasonCode.POLICY_CHANGE.value
    assert body["retired"]["reason_code"] == RetireReasonCode.POLICY_CHANGE.value
    states = [t["to_state"] for t in body["transitions"]]
    assert states == [
        CaseWorkflowState.IN_REVIEW.value,
        CaseWorkflowState.APPROVED.value,
        CaseWorkflowState.PUBLISHED.value,
        CaseWorkflowState.RETIRED.value,
    ]
    times = [t["occurred_at"] for t in body["transitions"]]
    assert times == sorted(times)


def test_http_cross_tenant_published_get_404(slice4_client):
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions/C1/1.0.0",
        headers=_dev_auth(CATALOG_READER, tenant=OTHER_TENANT),
    )
    assert resp.status_code == 404


def test_metrics_endpoint_includes_authoring_metrics(slice4_client):
    _http_publish_retire(slice4_client, version="3.0.0")
    resp = slice4_client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "authoring_publish_total" in body
    assert "authoring_retire_total" in body
    assert "authoring_published_active_total" in body
    assert "authoring_retired_total" in body
    assert render_prometheus_metrics() in body or "audit_sink_emit_total" in body
