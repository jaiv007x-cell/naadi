"""Ledger read HTTP API — JWT auth, grant-store consent, privacy boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.api.deps import get_jwt_gateway
from services.pratibimb.app.main import app
from services.pratibimb.ledger.models import (
    Base,
    CohortUnitAssignmentRow,
    InstitutionalUnit,
    SessionLedgerRow,
    TrustedPhysioVersion,
)
from services.pratibimb.ledger_read.deps import (
    get_in_memory_consent_store,
    reset_auth_wiring_cache,
    set_consent_store,
)
from services.pratibimb.ledger_read.errors import InsufficientCohortError, ScopeDeniedError
from services.pratibimb.ledger_read.jwt_gateway import JwtLedgerGateway
from services.pratibimb.tests.test_auth_jwt import (
    AUD,
    ISS,
    JWKS_URL,
    TENANT,
    _claims_payload,
    _sign,
    rsa_keypair,
)
from shared.schemas.consent import ConsentGrantSource
from shared.schemas.flag_cause import FlagCause
from shared.schemas.ledger_read import ConsentScope, LearnerEvidenceResponse, UnitSafetyReport
from shared.schemas.unit_ref import UnitType


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch, rsa_keypair):
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "1")
    monkeypatch.setenv("CONSENT_STORE", "in_memory")
    monkeypatch.setenv(
        "TENANT_TRUST_CONFIG",
        f"tenant={TENANT},iss={ISS},aud={AUD},jwks={JWKS_URL}",
    )
    monkeypatch.delenv("TENANT_JWKS_URLS", raising=False)
    reset_auth_wiring_cache()
    from services.pratibimb.ledger_read.deps import get_jwks_cache

    get_jwks_cache().seed(JWKS_URL, [rsa_keypair[0]])
    yield
    reset_auth_wiring_cache()


def _dev_auth(sub: str, tenant: str = TENANT) -> dict[str, str]:
    return {"X-Dev-Subject": sub, "X-Dev-Tenant": tenant}


def _bearer(sub: str, rsa_keypair, *, tenant: str = TENANT) -> dict[str, str]:
    _, private_pem = rsa_keypair
    token = _sign(_claims_payload(sub=sub), private_pem)
    return {"Authorization": f"Bearer {token}"}


def _mk_row(session_id: str, *, learner: str = "L1", cohort: str = "cohortA") -> SessionLedgerRow:
    return SessionLedgerRow(
        session_id=session_id,
        learner_pseudo_id=learner,
        cohort_id=cohort,
        case_id="anaphylaxis_01",
        case_version="1.0",
        physio_engine_version="physio@v1",
        rubric_version="rub1",
        replay_hash=f"hash-{session_id}",
        finalized_at_utc=datetime(2026, 8, 15, tzinfo=timezone.utc),
        confirmation="confirmed",
        preceptor_pseudo_id=None,
        grade_total=0.8,
        grade_passed=True,
        axis_normalized={"SAFETY": 0.9},
        evidence=[],
        flags=[
            {
                "flag": "hypotension",
                "cause": FlagCause.DRUG_PD_EFFECT.value,
                "set_at_s": 60.0,
                "cleared_at_s": 120.0,
                "clear_reason": None,
            }
        ],
        actions=[],
        outcome_link_token=None,
    )


async def _seed_self_learner_grant(sub: str) -> None:
    store = get_in_memory_consent_store()
    await store.issue_grant(
        tenant_id=TENANT,
        subject_id=sub,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=sub,
        granted_by="test",
        source=ConsentGrantSource.LEARNER_PORTAL,
        granted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


async def _seed_preceptor_grant(preceptor: str, learner: str) -> None:
    store = get_in_memory_consent_store()
    await store.issue_grant(
        tenant_id=TENANT,
        subject_id=preceptor,
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id=learner,
        granted_by="test",
        source=ConsentGrantSource.PRECEPTOR_CONSOLE,
        granted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


async def _seed_aggregate_grant(subject: str, resource_id: str) -> None:
    store = get_in_memory_consent_store()
    await store.issue_grant(
        tenant_id=TENANT,
        subject_id=subject,
        scope=ConsentScope.AGGREGATE_ANALYTICS,
        resource_id=resource_id,
        granted_by="test",
        source=ConsentGrantSource.INSTITUTIONAL_ADMIN,
        granted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class _FakeGateway:
    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self._raise = raise_exc

    async def learner_evidence(self, auth, filt):
        if self._raise:
            raise self._raise
        return LearnerEvidenceResponse(
            learner_pseudo_id=filt.learner_pseudo_id,
            events=[],
            total=0,
        )

    async def unit_safety_report(self, auth, filt):
        if self._raise:
            raise self._raise
        return UnitSafetyReport(
            unit_id=filt.unit_id or "",
            window_days=filt.window_days,
            cohort_size=75,
            critical_violation_rate=0.04,
            top_causes=[],
            generated_at=datetime.now(timezone.utc),
        )


@pytest.fixture
def client():
    from services.pratibimb.ledger_read.consent_store import InMemoryConsentGrantStore

    set_consent_store(InMemoryConsentGrantStore())
    return TestClient(app)


def test_missing_bearer_returns_401(client, monkeypatch):
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "0")
    reset_auth_wiring_cache()
    r = client.post("/v1/ledger/evidence/learner", json={"learner_pseudo_id": "L1"})
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "missing_bearer"


def test_invalid_jwt_returns_401(client, monkeypatch):
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "0")
    reset_auth_wiring_cache()
    r = client.post(
        "/v1/ledger/evidence/learner",
        json={"learner_pseudo_id": "L1"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_token"


@pytest.mark.asyncio
async def test_no_grant_returns_403(client, rsa_keypair):
    r = client.post(
        "/v1/ledger/evidence/learner",
        json={"learner_pseudo_id": "L42"},
        headers=_bearer("preceptor7", rsa_keypair),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "scope_denied"


@pytest.mark.asyncio
async def test_learner_evidence_happy_path(client, rsa_keypair):
    await _seed_preceptor_grant("preceptor7", "L42")
    app.dependency_overrides[get_jwt_gateway] = lambda: _FakeGateway()
    try:
        r = client.post(
            "/v1/ledger/evidence/learner",
            json={"learner_pseudo_id": "L42"},
            headers=_bearer("preceptor7", rsa_keypair),
        )
        assert r.status_code == 200
        assert r.json()["learner_pseudo_id"] == "L42"
    finally:
        app.dependency_overrides.clear()


def test_scope_denied_maps_to_403(client):
    app.dependency_overrides[get_jwt_gateway] = lambda: _FakeGateway(
        raise_exc=ScopeDeniedError(
            required=frozenset({ConsentScope.PRECEPTOR_REVIEW}),
            granted=frozenset({ConsentScope.AGGREGATE_ANALYTICS}),
        )
    )
    try:
        r = client.post(
            "/v1/ledger/evidence/learner",
            json={"learner_pseudo_id": "L42"},
            headers=_dev_auth("analyst1"),
        )
        assert r.status_code == 403
        body = r.json()["detail"]
        assert body["error"] == "scope_denied"
        assert ConsentScope.PRECEPTOR_REVIEW.value in body["required_scopes"]
    finally:
        app.dependency_overrides.clear()


def test_insufficient_cohort_does_not_leak_actual_k(client):
    app.dependency_overrides[get_jwt_gateway] = lambda: _FakeGateway(
        raise_exc=InsufficientCohortError(minimum_required=50)
    )
    try:
        r = client.post(
            "/v1/ledger/reports/unit_safety",
            json={"unit_id": "ward_3B"},
            headers=_dev_auth("analyst1"),
        )
        assert r.status_code == 403
        body = r.json()["detail"]
        assert body["error"] == "insufficient_cohort"
        assert body["minimum_required"] == 50
        assert "actual_k" not in body
        assert "cohort_size" not in body
    finally:
        app.dependency_overrides.clear()


def test_expired_token_returns_401(client, rsa_keypair, monkeypatch):
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "0")
    reset_auth_wiring_cache()
    from services.pratibimb.ledger_read.deps import get_jwks_cache

    get_jwks_cache().seed(JWKS_URL, [rsa_keypair[0]])
    _, private_pem = rsa_keypair
    token = _sign(_claims_payload(ttl_s=-10), private_pem)
    r = client.post(
        "/v1/ledger/evidence/learner",
        json={"learner_pseudo_id": "L1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "token_expired"


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    db_path = tmp_path / "ledger_api.db"
    monkeypatch.setenv("LEDGER_DATABASE_URL", f"sqlite:///{db_path}")
    from services.pratibimb.ledger import db as ledger_db

    ledger_db.get_engine.cache_clear()

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        for i in range(55):
            s.add(_mk_row(f"s{i}", learner=f"L{i % 5}"))
        s.add(
            InstitutionalUnit(
                tenant_id=TENANT,
                unit_id="ward-emergency",
                unit_type=UnitType.WARD.value,
                display_name="Emergency Ward",
            )
        )
        s.add(
            CohortUnitAssignmentRow(
                tenant_id=TENANT,
                cohort_id="cohortA",
                unit_id="ward-emergency",
            )
        )
        s.commit()

    yield TestClient(app)
    ledger_db.get_engine.cache_clear()


@pytest.mark.asyncio
async def test_integration_learner_evidence(api_client):
    await _seed_self_learner_grant("L1")
    resp = api_client.get(
        "/v1/ledger/learners/L1/evidence",
        headers=_dev_auth("L1"),
    )
    assert resp.status_code == 200
    assert resp.json()


@pytest.mark.asyncio
async def test_integration_cohort_summary(api_client):
    await _seed_aggregate_grant("admin-1", "cohort:cohortA")
    resp = api_client.get(
        "/v1/ledger/cohorts/cohortA/summary",
        headers=_dev_auth("admin-1"),
    )
    assert resp.status_code == 200
    assert resp.json()["cohort_id"] == "cohortA"


@pytest.mark.asyncio
async def test_integration_unit_safety(api_client):
    await _seed_aggregate_grant("admin-1", "unit:ward-emergency")
    resp = api_client.get(
        "/v1/ledger/units/ward-emergency/safety",
        headers=_dev_auth("admin-1"),
    )
    assert resp.status_code == 200
    assert resp.json()["unit_id"] == "ward-emergency"


@pytest.mark.asyncio
async def test_revoked_grant_denied_on_next_request(client, rsa_keypair):
    store = get_in_memory_consent_store()
    grant = await store.issue_grant(
        tenant_id=TENANT,
        subject_id="preceptor7",
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id="L42",
        granted_by="test",
        source=ConsentGrantSource.PRECEPTOR_CONSOLE,
        granted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    headers = _bearer("preceptor7", rsa_keypair)
    assert client.post(
        "/v1/ledger/evidence/learner",
        json={"learner_pseudo_id": "L42"},
        headers=headers,
    ).status_code == 200

    await store.revoke_grant(
        grant.grant_id,
        revoked_by="admin",
        revoke_reason_code="withdrawn",
        revoked_at=datetime.now(timezone.utc),
    )
    assert client.post(
        "/v1/ledger/evidence/learner",
        json={"learner_pseudo_id": "L42"},
        headers=headers,
    ).status_code == 403
