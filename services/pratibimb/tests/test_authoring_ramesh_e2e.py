"""
Ramesh Kale STEMI — end-to-end authoring smoke regression.

Mirrors docs/authoring/ramesh_smoke.md through the test client.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
from services.pratibimb.authoring.ramesh_smoke import (
    RAMESH_CASE_VERSION,
    RAMESH_SMOKE_AUTHOR,
    attach_smoke_rubric,
    ramesh_critical_miss_trace,
    ramesh_golden_pass_trace,
)
from services.pratibimb.authoring.seed import seed_ramesh_draft
from services.pratibimb.ledger_read.deps import (
    get_jwks_cache,
    reset_auth_wiring_cache,
    set_consent_store,
)
from services.pratibimb.ledger_read.consent_store import InMemoryConsentGrantStore
from services.pratibimb.tests.test_auth_jwt import AUD, ISS, JWKS_URL, rsa_keypair
from shared.schemas.ledger_read import ConsentScope

PRIYA = "priya-reviewer"


def _dev_auth(sub: str = RAMESH_SMOKE_AUTHOR, tenant: str = RAMESH_TENANT_ID) -> dict[str, str]:
    return {"X-Dev-Subject": sub, "X-Dev-Tenant": tenant}


@pytest.fixture
def ramesh_e2e_client(monkeypatch, tmp_path, rsa_keypair):
    db_url = f"sqlite:///{tmp_path / 'ramesh_e2e.db'}"
    monkeypatch.setenv("AUTHORING_DATABASE_URL", db_url)
    monkeypatch.setenv("LEDGER_DATABASE_URL", f"sqlite:///{tmp_path / 'ledger_e2e.db'}")
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "1")
    monkeypatch.setenv("CONSENT_STORE", "in_memory")
    monkeypatch.setenv(
        "TENANT_TRUST_CONFIG",
        f"tenant={RAMESH_TENANT_ID},iss={ISS},aud={AUD},jwks={JWKS_URL}",
    )
    reset_auth_wiring_cache()
    reset_authoring_wiring_cache()
    set_consent_store(InMemoryConsentGrantStore())
    get_jwks_cache().seed(JWKS_URL, [rsa_keypair[0]])

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
        seed_ramesh_draft(session, wire_smoke=False)

    asyncio.run(
        seed_authoring_scope(
            tenant_id=RAMESH_TENANT_ID,
            subject_id=RAMESH_SMOKE_AUTHOR,
            scope=ConsentScope.AUTHORING_SUBMIT,
        )
    )
    asyncio.run(
        seed_authoring_scope(
            tenant_id=RAMESH_TENANT_ID,
            subject_id=PRIYA,
            scope=ConsentScope.AUTHORING_APPROVE,
        )
    )

    yield TestClient(app)

    app.dependency_overrides.clear()
    reset_auth_wiring_cache()
    reset_authoring_wiring_cache()


def test_ramesh_authoring_smoke_workflow(ramesh_e2e_client: TestClient):
    client = ramesh_e2e_client
    headers = _dev_auth()

    # Step 1 — GET baseline
    baseline = client.get(f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}", headers=headers)
    assert baseline.status_code == 200
    base_body = baseline.json()
    assert base_body["current_state"] == CaseWorkflowState.DRAFT.value
    assert base_body["case_id"] == RAMESH_CASE_ID
    assert len(base_body["content_hash"]) == 64
    hash_before_rubric = base_body["content_hash"]

    # Step 2 — PUT rubric
    blueprint_with_rubric = attach_smoke_rubric(base_body["blueprint_json"])
    updated = client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}",
        headers=headers,
        json={
            "blueprint_json": blueprint_with_rubric,
            "blueprint_version": RAMESH_CASE_VERSION,
        },
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["blueprint_json"]["grading_blueprint"]["hits"]
    assert len(updated_body["blueprint_json"]["grading_blueprint"]["hits"]) == 3
    assert updated_body["content_hash"] != hash_before_rubric

    # Step 3 — PUT fixture pair
    perfect = client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/fixtures",
        headers=headers,
        json={
            "fixture_kind": FixtureKind.PERFECT_PATH.value,
            "trace_json": ramesh_golden_pass_trace(),
            "expected_grade_json": {"passed": True},
        },
    )
    assert perfect.status_code == 200
    assert len(perfect.json()["content_hash"]) == 64

    critical = client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/fixtures",
        headers=headers,
        json={
            "fixture_kind": FixtureKind.CRITICAL_MISS.value,
            "trace_json": ramesh_critical_miss_trace(),
            "expected_grade_json": {"passed": False},
        },
    )
    assert critical.status_code == 200

    # Step 4 — dry-run (stable hash)
    dry1 = client.post(f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/dry-run", headers=headers)
    assert dry1.status_code == 200
    dry1_body = dry1.json()
    assert dry1_body["passed"] is True
    assert len(dry1_body["result_hash"]) == 64
    by_kind = {o["fixture_kind"]: o for o in dry1_body["outcomes"]}
    assert by_kind["perfect_path"]["matched_expectation"] is True
    assert by_kind["perfect_path"]["actual_passed"] is True
    assert by_kind["critical_miss"]["matched_expectation"] is True
    assert by_kind["critical_miss"]["actual_passed"] is False

    dry2 = client.post(f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/dry-run", headers=headers)
    assert dry2.status_code == 200
    assert dry2.json()["result_hash"] == dry1_body["result_hash"]

    # Step 5 — submit
    submitted = client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={"reason": "smoke walkthrough"},
    )
    assert submitted.status_code == 200
    sub_body = submitted.json()
    assert sub_body["current_state"] == CaseWorkflowState.IN_REVIEW.value
    transition = sub_body["transitions"][-1]
    assert transition["from_state"] == CaseWorkflowState.DRAFT.value
    assert transition["to_state"] == CaseWorkflowState.IN_REVIEW.value
    assert transition["dry_run_result_hash"] == dry1_body["result_hash"]

    # Step 6 — revert
    reverted = client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/revert",
        headers=headers,
        json={"reason": "smoke complete"},
    )
    assert reverted.status_code == 200
    rev_body = reverted.json()
    assert rev_body["current_state"] == CaseWorkflowState.DRAFT.value
    rev_transition = rev_body["transitions"][-1]
    assert rev_transition["to_state"] == CaseWorkflowState.DRAFT.value
    assert rev_transition["dry_run_result_hash"] is None

    # Edits blocked while in review — sanity on revert path
    client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={},
    ).raise_for_status()
    blocked = client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}",
        headers=headers,
        json={
            "blueprint_json": blueprint_with_rubric,
            "blueprint_version": RAMESH_CASE_VERSION,
        },
    )
    assert blocked.status_code == 409


def _attach_rubric_and_fixtures(client: TestClient) -> dict:
    headers = _dev_auth()
    baseline = client.get(f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}", headers=headers)
    blueprint = attach_smoke_rubric(baseline.json()["blueprint_json"])
    client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}",
        headers=headers,
        json={"blueprint_json": blueprint, "blueprint_version": RAMESH_CASE_VERSION},
    ).raise_for_status()
    for kind, trace, passed in (
        (FixtureKind.PERFECT_PATH, ramesh_golden_pass_trace(), True),
        (FixtureKind.CRITICAL_MISS, ramesh_critical_miss_trace(), False),
    ):
        client.put(
            f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/fixtures",
            headers=headers,
            json={
                "fixture_kind": kind.value,
                "trace_json": trace,
                "expected_grade_json": {"passed": passed},
            },
        ).raise_for_status()
    return blueprint


def test_submit_without_scope_403(ramesh_e2e_client: TestClient):
    _attach_rubric_and_fixtures(ramesh_e2e_client)
    resp = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=_dev_auth("no-scope-user"),
        json={},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "SCOPE_DENIED"


def test_submit_missing_fixtures_409(ramesh_e2e_client: TestClient):
    headers = _dev_auth()
    baseline = ramesh_e2e_client.get(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}", headers=headers
    )
    blueprint = attach_smoke_rubric(baseline.json()["blueprint_json"])
    ramesh_e2e_client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}",
        headers=headers,
        json={"blueprint_json": blueprint, "blueprint_version": RAMESH_CASE_VERSION},
    ).raise_for_status()

    resp = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "MISSING_FIXTURES"


def test_submit_dry_run_fail_409_no_transition(ramesh_e2e_client: TestClient):
    headers = _dev_auth()
    _attach_rubric_and_fixtures(ramesh_e2e_client)
    ramesh_e2e_client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/fixtures",
        headers=headers,
        json={
            "fixture_kind": FixtureKind.PERFECT_PATH.value,
            "trace_json": ramesh_critical_miss_trace(),
            "expected_grade_json": {"passed": True},
        },
    ).raise_for_status()

    resp = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "DRY_RUN_FAILED"

    draft = ramesh_e2e_client.get(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}", headers=headers
    ).json()
    assert draft["current_state"] == CaseWorkflowState.DRAFT.value
    assert all(row["to_state"] != CaseWorkflowState.IN_REVIEW.value for row in draft["transitions"])


def test_approver_distinct_fires_before_phase_c_gate(ramesh_e2e_client: TestClient):
    headers = _dev_auth()
    _attach_rubric_and_fixtures(ramesh_e2e_client)
    ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={},
    ).raise_for_status()

    resp = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/approve",
        headers=headers,
        json={},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "AUTHOR_CANNOT_APPROVE"


def test_approve_by_distinct_reviewer(ramesh_e2e_client: TestClient):
    headers = _dev_auth()
    _attach_rubric_and_fixtures(ramesh_e2e_client)
    submitted = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={},
    )
    submitted.raise_for_status()
    content_hash = submitted.json()["content_hash"]

    resp = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/approve",
        headers=_dev_auth(PRIYA),
        json={"reason": "formative sign-off"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_state"] == CaseWorkflowState.APPROVED.value
    latest = body["transitions"][-1]
    assert latest["from_state"] == CaseWorkflowState.IN_REVIEW.value
    assert latest["to_state"] == CaseWorkflowState.APPROVED.value
    assert latest["actor_subject_id"] == PRIYA
    assert latest["content_hash_snapshot"] == content_hash
    submit_row = submitted.json()["transitions"][-1]
    assert latest["content_hash_snapshot"] == submit_row["content_hash_snapshot"]
    assert latest["validation_context_hash"] == submit_row["validation_context_hash"]
    assert latest["validation_context_hash"]
    submit_hash = submit_row["dry_run_result_hash"]
    assert latest["dry_run_result_hash"] == submit_hash
    assert body["approvals"][0]["kind"] == "FORMATIVE"
    assert body["approvals"][0]["submit_dry_run_hash"] == submit_hash


def test_approve_scope_does_not_satisfy_summative(ramesh_e2e_client: TestClient):
    headers = _dev_auth()
    _attach_rubric_and_fixtures(ramesh_e2e_client)
    ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={},
    ).raise_for_status()
    resp = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/approve-summative",
        headers=_dev_auth(PRIYA),
        json={},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "SCOPE_DENIED"


def test_bad_action_id_saves_but_blocks_submit(ramesh_e2e_client: TestClient):
    headers = _dev_auth()
    baseline = ramesh_e2e_client.get(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}", headers=headers
    )
    blueprint = attach_smoke_rubric(baseline.json()["blueprint_json"])
    blueprint["grading_blueprint"]["hits"].append(
        {
            "id": "timing.bad_action",
            "axis": "timing",
            "matcher": "action_within",
            "params": {"action_id": "not_a_real_action", "within_s": 60},
            "points": 1.0,
        }
    )
    saved = ramesh_e2e_client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}",
        headers=headers,
        json={"blueprint_json": blueprint, "blueprint_version": RAMESH_CASE_VERSION},
    )
    assert saved.status_code == 200
    assert saved.json()["current_state"] == CaseWorkflowState.DRAFT.value
    assert any("unregistered action id" in e for e in saved.json()["validation_errors"])
    issues = saved.json()["validation_issues"]
    assert any(i["kind"] == "compile" and i["code"] == "UNREGISTERED_ACTION_ID" for i in issues)

    for kind, trace, passed in (
        (FixtureKind.PERFECT_PATH, ramesh_golden_pass_trace(), True),
        (FixtureKind.CRITICAL_MISS, ramesh_critical_miss_trace(), False),
    ):
        ramesh_e2e_client.put(
            f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/fixtures",
            headers=headers,
            json={
                "fixture_kind": kind.value,
                "trace_json": trace,
                "expected_grade_json": {"passed": passed},
            },
        ).raise_for_status()

    blocked = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "BLUEPRINT_INVALID"

    blueprint["grading_blueprint"]["hits"][-1]["params"]["action_id"] = "give_aspirin_325_chewed"
    blueprint["grading_blueprint"]["hits"][-1]["params"]["inner_hit"] = "stemi.ecg_ordered"
    ramesh_e2e_client.put(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}",
        headers=headers,
        json={"blueprint_json": blueprint, "blueprint_version": RAMESH_CASE_VERSION},
    ).raise_for_status()

    submitted = ramesh_e2e_client.post(
        f"/v1/authoring/drafts/{RAMESH_DRAFT_ID}/submit",
        headers=headers,
        json={},
    )
    assert submitted.status_code == 200
    assert submitted.json()["current_state"] == CaseWorkflowState.IN_REVIEW.value
