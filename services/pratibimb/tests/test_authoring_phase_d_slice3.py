"""Phase D slice 3: retire preconditions, store txn, HTTP publish/retire routes."""
from __future__ import annotations

import asyncio
import copy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
]

from services.pratibimb.app.eval.rubric import Axis, Severity
from services.pratibimb.app.main import app
from services.pratibimb.authoring.constants import (
    CaseWorkflowState,
    FixtureKind,
    PublishPreconditionCode,
    RetirePreconditionCode,
    RetireReasonCode,
    SCOPE_APPROVE,
    SCOPE_APPROVE_SUMMATIVE,
)
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.deps import (
    authoring_db,
    reset_authoring_wiring_cache,
    seed_authoring_scope,
)
from services.pratibimb.authoring.errors import (
    InvalidStateTransitionError,
    PublisherCannotRetireError,
    RetirePreconditionFailed,
    VersionStringBurnedError,
)
from services.pratibimb.authoring.state_machine import TransitionActor
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.ledger_read.consent_store import InMemoryConsentGrantStore
from services.pratibimb.ledger_read.deps import (
    get_jwks_cache,
    reset_auth_wiring_cache,
    set_consent_store,
)
from services.pratibimb.tests.test_auth_jwt import AUD, ISS, JWKS_URL, TENANT, rsa_keypair
from services.pratibimb.tests.test_authoring_phase_a import (
    AUTHOR,
    REVIEWER,
    TENANT as STORE_TENANT,
    _miss_trace,
    _pass_trace,
)
from services.pratibimb.tests.test_authoring_phase_c import REVIEWER_B, _submit
from shared.schemas.ledger_read import ConsentScope

PUBLISHER = "publisher-5"
RETIRER = "retirer-6"
GUIDELINE_PIN = "acc-2024@sha256:" + ("a" * 64)


def _full_blueprint(*, assessment_mode: str = "formative", corpus_tier: str = "silver"):
    bp = {
        "identity": {
            "case_id": "C1",
            "version": "1.0.0",
            "title": "Test case",
            "corpus_tier": corpus_tier,
            "assessment_mode": assessment_mode,
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
            "case_id": "C1",
            "case_version": "1.0.0",
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
                },
                {
                    "id": "safety.check",
                    "axis": Axis.SAFETY.value,
                    "matcher": "drug_given",
                    "params": {"drug_id": "aspirin"},
                    "points": 5.0,
                    "severity": Severity.CRITICAL.value,
                    "required": True,
                    "fail_case_on_violation": True,
                },
            ],
        },
    }
    return bp


def _seed_full_draft(store: CaseDraftStore, *, assessment_mode: str = "formative"):
    draft = store.create_draft(
        tenant_id=STORE_TENANT,
        author_subject_id=AUTHOR,
        blueprint_json=_full_blueprint(assessment_mode=assessment_mode),
        blueprint_version="1.0.0",
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
    return draft


def _approve(store: CaseDraftStore, draft_id: str):
    return store.transition(
        draft_id,
        to_state=CaseWorkflowState.APPROVED,
        actor=TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE})),
    )


def _summative_grants(store: CaseDraftStore, draft_id: str):
    first = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}))
    second = TransitionActor(subject_id=REVIEWER_B, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}))
    store.record_summative_approval(draft_id, actor=first)
    store.record_summative_approval(draft_id, actor=second)


def _publish_formative(store: CaseDraftStore, draft_id: str):
    return store.publish_draft(
        draft_id,
        actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
    )


def test_retire_happy_path_writes_tombstone_and_audit(store, authoring_session):
    draft = _seed_full_draft(store)
    _submit(store, draft.id)
    _approve(store, draft.id)
    _publish_formative(store, draft.id)

    result = store.retire_published(
        draft.id,
        actor=TransitionActor(subject_id=RETIRER, scopes=frozenset({SCOPE_APPROVE})),
        reason_code=RetireReasonCode.POLICY_CHANGE.value,
        reason_note="policy update",
    )
    authoring_session.commit()

    refreshed = store.get_draft(draft.id)
    assert refreshed.current_state == CaseWorkflowState.RETIRED.value
    assert result.published.retired_at is not None
    assert result.published.retired_by == RETIRER
    assert result.published.retired_reason_code == RetireReasonCode.POLICY_CHANGE.value
    assert result.published.retired_reason_text == "policy update"
    assert result.retired.reason_code == RetireReasonCode.POLICY_CHANGE.value
    assert result.retired.retired_by == RETIRER
    audits = store.list_retired_for_draft(draft.id)
    assert len(audits) == 1
    assert audits[0].id == result.retired.id


@pytest.mark.parametrize("reason", list(RetireReasonCode))
def test_retire_accepts_each_reason_code(store, authoring_session, reason: RetireReasonCode):
    draft = _seed_full_draft(store)
    bp = copy.deepcopy(draft.blueprint_json)
    bp["identity"]["version"] = f"1.0.{reason.value}"
    draft.blueprint_json = bp
    authoring_session.flush()
    _submit(store, draft.id)
    _approve(store, draft.id)
    _publish_formative(store, draft.id)

    result = store.retire_published(
        draft.id,
        actor=TransitionActor(subject_id=RETIRER, scopes=frozenset({SCOPE_APPROVE})),
        reason_code=reason.value,
    )
    assert result.retired.reason_code == reason.value


def test_retire_rejects_publisher_as_retirer(store, authoring_session):
    draft = _seed_full_draft(store)
    _submit(store, draft.id)
    _approve(store, draft.id)
    _publish_formative(store, draft.id)

    with pytest.raises(PublisherCannotRetireError):
        store.retire_published(
            draft.id,
            actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
            reason_code=RetireReasonCode.OPERATOR_REQUEST.value,
        )


def test_retire_rejects_wrong_state(store, authoring_session):
    draft = _seed_full_draft(store)
    actor = TransitionActor(subject_id=RETIRER, scopes=frozenset({SCOPE_APPROVE}))
    for bad in (
        CaseWorkflowState.DRAFT,
        CaseWorkflowState.IN_REVIEW,
        CaseWorkflowState.APPROVED,
    ):
        draft.current_state = bad.value
        authoring_session.flush()
        with pytest.raises(InvalidStateTransitionError) as exc:
            store.retire_published(
                draft.id,
                actor=actor,
                reason_code=RetireReasonCode.CLINICAL_ERROR.value,
            )
        assert exc.value.code == PublishPreconditionCode.WRONG_STATE.value


def test_retire_rejects_missing_reason_code(store, authoring_session):
    draft = _seed_full_draft(store)
    _submit(store, draft.id)
    _approve(store, draft.id)
    _publish_formative(store, draft.id)

    with pytest.raises(RetirePreconditionFailed) as exc:
        store.retire_published(
            draft.id,
            actor=TransitionActor(subject_id=RETIRER, scopes=frozenset({SCOPE_APPROVE})),
            reason_code=None,
        )
    assert exc.value.precondition_code == RetirePreconditionCode.INVALID_REASON_CODE.value


def test_version_burn_survives_retire_republish_attempt(store, authoring_session):
    draft = _seed_full_draft(store)
    case_id = draft.blueprint_json["identity"]["case_id"]
    version = draft.blueprint_json["identity"]["version"]
    _submit(store, draft.id)
    _approve(store, draft.id)
    _publish_formative(store, draft.id)
    store.retire_published(
        draft.id,
        actor=TransitionActor(subject_id=RETIRER, scopes=frozenset({SCOPE_APPROVE})),
        reason_code=RetireReasonCode.SUPERSEDED_BY_NEW_VERSION.value,
    )
    authoring_session.commit()

    draft2 = _seed_full_draft(store)
    assert draft2.blueprint_json["identity"]["case_id"] == case_id
    assert draft2.blueprint_json["identity"]["version"] == version
    _submit(store, draft2.id)
    _approve(store, draft2.id)
    with pytest.raises(VersionStringBurnedError):
        store.publish_draft(
            draft2.id,
            actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
        )


def test_transition_delegates_published_to_retired(store, authoring_session):
    draft = _seed_full_draft(store)
    _submit(store, draft.id)
    _approve(store, draft.id)
    _publish_formative(store, draft.id)
    transition = store.transition(
        draft.id,
        to_state=CaseWorkflowState.RETIRED,
        actor=TransitionActor(subject_id=RETIRER, scopes=frozenset({SCOPE_APPROVE})),
        reason_code=RetireReasonCode.GUIDELINE_CHANGE.value,
        reason_note="guideline bump",
    )
    assert transition.to_state == CaseWorkflowState.RETIRED.value
    assert store.get_draft(draft.id).current_state == CaseWorkflowState.RETIRED.value


def _dev_auth(sub: str, tenant: str = TENANT) -> dict[str, str]:
    return {"X-Dev-Subject": sub, "X-Dev-Tenant": tenant}


@pytest.fixture
def slice3_client(monkeypatch, tmp_path, rsa_keypair):
    db_url = f"sqlite:///{tmp_path / 'authoring_slice3.db'}"
    monkeypatch.setenv("AUTHORING_DATABASE_URL", db_url)
    monkeypatch.setenv("LEDGER_DATABASE_URL", f"sqlite:///{tmp_path / 'ledger_slice3.db'}")
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "1")
    monkeypatch.setenv("CONSENT_STORE", "in_memory")
    monkeypatch.setenv(
        "TENANT_TRUST_CONFIG",
        f"tenant={TENANT},iss={ISS},aud={AUD},jwks={JWKS_URL}",
    )
    reset_auth_wiring_cache()
    reset_authoring_wiring_cache()
    consent = InMemoryConsentGrantStore()
    set_consent_store(consent)
    get_jwks_cache().seed(JWKS_URL, [rsa_keypair[0]])

    for subject, scope in (
        (AUTHOR, ConsentScope.AUTHORING_SUBMIT),
        (REVIEWER, ConsentScope.AUTHORING_APPROVE),
        (REVIEWER, ConsentScope.AUTHORING_APPROVE_SUMMATIVE),
        (REVIEWER_B, ConsentScope.AUTHORING_APPROVE_SUMMATIVE),
        (PUBLISHER, ConsentScope.AUTHORING_APPROVE),
        (PUBLISHER, ConsentScope.AUTHORING_APPROVE_SUMMATIVE),
        (RETIRER, ConsentScope.AUTHORING_APPROVE),
        (RETIRER, ConsentScope.AUTHORING_APPROVE_SUMMATIVE),
    ):
        asyncio.run(
            seed_authoring_scope(
                tenant_id=TENANT,
                subject_id=subject,
                scope=scope,
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


def _http_seed_to_approved(client: TestClient, *, assessment_mode: str = "formative") -> str:
    bp = _full_blueprint(assessment_mode=assessment_mode)
    if assessment_mode == "summative":
        bp["identity"]["corpus_tier"] = "gold"
    created = client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": bp, "blueprint_version": "1.0.0"},
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]
    for kind, passed, trace in (
        (FixtureKind.PERFECT_PATH, True, _pass_trace()),
        (FixtureKind.CRITICAL_MISS, False, _miss_trace()),
    ):
        resp = client.put(
            f"/v1/authoring/drafts/{draft_id}/fixtures",
            headers=_dev_auth(AUTHOR),
            json={
                "fixture_kind": kind.value,
                "trace_json": trace,
                "expected_grade_json": {"passed": passed},
            },
        )
        assert resp.status_code == 200, resp.text
    submit = client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={},
    )
    assert submit.status_code == 200, submit.text
    if assessment_mode == "summative":
        for actor in (REVIEWER, REVIEWER_B):
            grant = client.post(
                f"/v1/authoring/drafts/{draft_id}/approve-summative",
                headers=_dev_auth(actor),
                json={},
            )
            assert grant.status_code == 200, grant.text
    approve = client.post(
        f"/v1/authoring/drafts/{draft_id}/approve",
        headers=_dev_auth(REVIEWER),
        json={},
    )
    assert approve.status_code == 200, approve.text
    return draft_id


def test_http_publish_happy_path_formative(slice3_client):
    draft_id = _http_seed_to_approved(slice3_client, assessment_mode="formative")
    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft"]["current_state"] == CaseWorkflowState.PUBLISHED.value
    assert body["published"]["published_by"] == PUBLISHER
    assert body["published"]["assessment_mode"] == "formative"
    assert body["published"]["case_id"] == "C1"


def test_http_publish_happy_path_summative(slice3_client):
    draft_id = _http_seed_to_approved(slice3_client, assessment_mode="summative")
    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft"]["current_state"] == CaseWorkflowState.PUBLISHED.value
    assert body["published"]["assessment_mode"] == "summative"
    assert body["published"]["clinical_reviewer"] == REVIEWER


@pytest.mark.parametrize("reason", list(RetireReasonCode))
def test_http_retire_happy_path_each_reason(slice3_client, reason: RetireReasonCode):
    bp = _full_blueprint()
    bp["identity"]["version"] = f"http-{reason.value}"
    created = slice3_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": bp, "blueprint_version": bp["identity"]["version"]},
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]
    for kind, passed, trace in (
        (FixtureKind.PERFECT_PATH, True, _pass_trace()),
        (FixtureKind.CRITICAL_MISS, False, _miss_trace()),
    ):
        slice3_client.put(
            f"/v1/authoring/drafts/{draft_id}/fixtures",
            headers=_dev_auth(AUTHOR),
            json={
                "fixture_kind": kind.value,
                "trace_json": trace,
                "expected_grade_json": {"passed": passed},
            },
        ).raise_for_status()
    slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={},
    ).raise_for_status()
    slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/approve",
        headers=_dev_auth(REVIEWER),
        json={},
    ).raise_for_status()
    slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    ).raise_for_status()

    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/retire",
        headers=_dev_auth(RETIRER),
        json={"reason_code": reason.value, "reason_note": f"note for {reason.value}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft"]["current_state"] == CaseWorkflowState.RETIRED.value
    assert body["retired"]["reason_code"] == reason.value
    assert body["retired"]["retired_by"] == RETIRER
    assert body["published"]["retired_reason_code"] == reason.value


def test_http_retire_rejects_publisher_equals_retirer(slice3_client):
    draft_id = _http_seed_to_approved(slice3_client)
    slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    ).raise_for_status()
    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/retire",
        headers=_dev_auth(PUBLISHER),
        json={"reason_code": RetireReasonCode.OPERATOR_REQUEST.value},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "PUBLISHER_CANNOT_RETIRE"


def test_http_retire_rejects_wrong_state(slice3_client):
    draft_id = _http_seed_to_approved(slice3_client)
    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/retire",
        headers=_dev_auth(RETIRER),
        json={"reason_code": RetireReasonCode.CLINICAL_ERROR.value},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == PublishPreconditionCode.WRONG_STATE.value
    assert resp.json()["detail"]["to_state"] == CaseWorkflowState.RETIRED.value


def test_http_retire_rejects_missing_reason_code(slice3_client):
    draft_id = _http_seed_to_approved(slice3_client)
    slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    ).raise_for_status()
    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/retire",
        headers=_dev_auth(RETIRER),
        json={},
    )
    assert resp.status_code == 422


def test_http_version_burn_after_retire(slice3_client):
    draft_id = _http_seed_to_approved(slice3_client)
    slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    ).raise_for_status()
    slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/retire",
        headers=_dev_auth(RETIRER),
        json={"reason_code": RetireReasonCode.SUPERSEDED_BY_NEW_VERSION.value},
    ).raise_for_status()

    draft2 = _http_seed_to_approved(slice3_client)
    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft2}/publish",
        headers=_dev_auth(PUBLISHER),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "VERSION_STRING_BURNED"


def test_http_error_mapping_approver_cannot_publish(slice3_client):
    draft_id = _http_seed_to_approved(slice3_client, assessment_mode="summative")
    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(REVIEWER),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "APPROVER_CANNOT_PUBLISH"


def test_http_error_mapping_publish_precondition_failed(slice3_client):
    # IN_REVIEW without approve → wrong state mapped as InvalidStateTransition → 422
    bp = _full_blueprint()
    created = slice3_client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": bp, "blueprint_version": "1.0.0"},
    )
    draft_id = created.json()["id"]
    for kind, passed, trace in (
        (FixtureKind.PERFECT_PATH, True, _pass_trace()),
        (FixtureKind.CRITICAL_MISS, False, _miss_trace()),
    ):
        slice3_client.put(
            f"/v1/authoring/drafts/{draft_id}/fixtures",
            headers=_dev_auth(AUTHOR),
            json={
                "fixture_kind": kind.value,
                "trace_json": trace,
                "expected_grade_json": {"passed": passed},
            },
        ).raise_for_status()
    slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={},
    ).raise_for_status()
    resp = slice3_client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == PublishPreconditionCode.WRONG_STATE.value
