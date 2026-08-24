"""ConsentGrantStore parity, point-in-time resolution, and startup guards."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.main import app
from services.pratibimb.ledger.models import Base, ConsentGrantRow
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.errors import ScopeDeniedError
from services.pratibimb.ledger_read.consent_startup import (
    ConsentStartupError,
    StartupConfigurationError,
    validate_consent_startup,
)
from services.pratibimb.ledger_read.consent_store import (
    InMemoryConsentGrantStore,
    PostgresConsentGrantStore,
)
from shared.schemas.consent import ConsentGrantEventType, ConsentGrantSource
from shared.schemas.ledger_read import ConsentScope

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)
T3 = T0 + timedelta(hours=3)

TENANT = "tenant-a"
SUBJECT = "learner-42"
OTHER_TENANT = "tenant-b"


class StoreFactory:
    def __init__(self, name: str, store, session=None) -> None:
        self.name = name
        self.store = store
        self.session = session


@pytest.fixture(params=["memory", "postgres"])
async def grant_store(request) -> AsyncIterator[StoreFactory]:
    if request.param == "memory":
        yield StoreFactory("memory", InMemoryConsentGrantStore())
        return

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield StoreFactory("postgres", PostgresConsentGrantStore(session), session)
    session.close()


@pytest.mark.asyncio
async def test_point_in_time_resolution(grant_store: StoreFactory):
    store = grant_store.store
    grant = await store.issue_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        granted_by="admin",
        source=ConsentGrantSource.LEARNER_PORTAL,
        granted_at=T1,
    )

    assert not await store.has_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        at=T0,
    )
    assert await store.has_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        at=T1 + timedelta(minutes=30),
    )

    await store.revoke_grant(
        grant.grant_id,
        revoked_by="admin",
        revoke_reason_code="withdrawn",
        revoked_at=T2,
    )

    assert await store.has_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        at=T1 + timedelta(minutes=30),
    )
    assert not await store.has_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        at=T3,
    )


@pytest.mark.asyncio
async def test_resolve_scope_point_in_time(grant_store: StoreFactory):
    store = grant_store.store
    await store.issue_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        granted_by="admin",
        source=ConsentGrantSource.LEARNER_PORTAL,
        granted_at=T1,
    )
    preceptor = await store.issue_grant(
        tenant_id=TENANT,
        subject_id="preceptor-1",
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id=SUBJECT,
        granted_by="admin",
        source=ConsentGrantSource.PRECEPTOR_CONSOLE,
        granted_at=T1,
    )

    assert await store.resolve_scope(SUBJECT, TENANT, T0) == frozenset()
    active = await store.resolve_scope(SUBJECT, TENANT, T2)
    assert ConsentScope.SELF_LEARNER in active

    preceptor_scopes = await store.resolve_scope("preceptor-1", TENANT, T2)
    assert preceptor_scopes == frozenset({ConsentScope.PRECEPTOR_REVIEW})

    await store.revoke_grant(
        preceptor.grant_id,
        revoked_by="admin",
        revoke_reason_code="rotation_ended",
        revoked_at=T2,
    )
    assert await store.resolve_scope("preceptor-1", TENANT, T3) == frozenset()


@pytest.mark.asyncio
async def test_revocation_appends_history(grant_store: StoreFactory):
    store = grant_store.store
    grant = await store.issue_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.AGGREGATE_ANALYTICS,
        resource_id="aggregate",
        granted_by="admin",
        source=ConsentGrantSource.INSTITUTIONAL_ADMIN,
        granted_at=T1,
    )
    revoke = await store.revoke_grant(
        grant.grant_id,
        revoked_by="admin",
        revoke_reason_code="policy_change",
        revoked_at=T2,
    )
    assert revoke is not None
    assert revoke.event_type == ConsentGrantEventType.REVOKE
    assert revoke.supersedes_grant_id == grant.grant_id

    if grant_store.name == "memory":
        assert len(grant_store.store._events) == 2
        assert grant_store.store._events[0].grant_id == grant.grant_id
        assert grant_store.store._events[1].revoke_reason_code == "policy_change"
    else:
        assert isinstance(store, PostgresConsentGrantStore)
        assert store.event_count() == 2
        rows = grant_store.session.execute(select(ConsentGrantRow)).scalars().all()
        assert len(rows) == 2
        original = next(r for r in rows if r.grant_id == grant.grant_id)
        assert original.event_type == ConsentGrantEventType.GRANT.value
        assert original.revoked_at is None


@pytest.mark.asyncio
async def test_grant_supersedes_prior_grant(grant_store: StoreFactory):
    store = grant_store.store
    first = await store.issue_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id="L1",
        granted_by="admin",
        source=ConsentGrantSource.PRECEPTOR_CONSOLE,
        granted_at=T1,
    )
    await store.issue_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id="L2",
        granted_by="admin",
        source=ConsentGrantSource.PRECEPTOR_CONSOLE,
        granted_at=T2,
        supersedes_grant_id=first.grant_id,
    )

    assert await store.has_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id="L1",
        at=T1 + timedelta(minutes=30),
    )
    assert not await store.has_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id="L1",
        at=T3,
    )
    assert await store.has_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id="L2",
        at=T3,
    )


@pytest.mark.asyncio
async def test_cross_tenant_isolation(grant_store: StoreFactory):
    store = grant_store.store
    await store.issue_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        granted_by="admin",
        source=ConsentGrantSource.LEARNER_PORTAL,
        granted_at=T1,
    )

    assert not await store.has_grant(
        tenant_id=OTHER_TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        at=T2,
    )
    assert await store.resolve_scope(SUBJECT, OTHER_TENANT, T2) == frozenset()


@pytest.mark.asyncio
async def test_wildcard_resource_id(grant_store: StoreFactory):
    store = grant_store.store
    await store.issue_grant(
        tenant_id=TENANT,
        subject_id="preceptor-9",
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id="*",
        granted_by="admin",
        source=ConsentGrantSource.INSTITUTIONAL_ADMIN,
        granted_at=T1,
    )

    assert await store.has_grant(
        tenant_id=TENANT,
        subject_id="preceptor-9",
        scope=ConsentScope.PRECEPTOR_REVIEW,
        resource_id="any-learner",
        at=T2,
    )


@pytest.mark.asyncio
async def test_consent_resolver_historical_at(grant_store: StoreFactory):
    store = grant_store.store
    await store.issue_grant(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        scope=ConsentScope.SELF_LEARNER,
        resource_id=SUBJECT,
        granted_by="admin",
        source=ConsentGrantSource.LEARNER_PORTAL,
        granted_at=T1,
    )
    resolver = ConsentResolver(store)
    auth = AuthContext(
        subject_pseudo_id=SUBJECT,
        tenant_id=TENANT,
        jti="test-jti",
        issued_at=T1,
        expires_at=T3,
        source="dev_header",
    )

    with pytest.raises(ScopeDeniedError):
        await resolver.for_learner(auth, SUBJECT, at=T0)

    scope = await resolver.for_learner(auth, SUBJECT, at=T2)
    assert scope == ConsentScope.SELF_LEARNER


def test_production_refuses_in_memory_consent_store(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("CONSENT_STORE", "in_memory")
    monkeypatch.setenv("DEV_HEADER_AUTH_ENABLED", "false")
    with pytest.raises(ConsentStartupError, match="forbidden"):
        validate_consent_startup()


def test_production_refuses_dev_header_auth_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("CONSENT_STORE", "postgres")
    monkeypatch.setenv("DEV_HEADER_AUTH_ENABLED", "true")
    with pytest.raises(StartupConfigurationError, match="DEV_HEADER_AUTH_ENABLED"):
        validate_consent_startup()


def test_development_allows_in_memory_consent_store(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("CONSENT_STORE", "in_memory")
    validate_consent_startup()


def test_production_allows_postgres_consent_store(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("CONSENT_STORE", "postgres")
    monkeypatch.setenv("DEV_HEADER_AUTH_ENABLED", "false")
    validate_consent_startup()


def test_production_refuses_dev_auth_headers_on_request(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("CONSENT_STORE", "postgres")
    monkeypatch.setenv("DEV_HEADER_AUTH_ENABLED", "false")
    client = TestClient(app)
    resp = client.get(
        "/health",
        headers={"X-Subject-Id": "dev-user", "X-Tenant-Id": "tenant-a"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "dev_auth_forbidden"
