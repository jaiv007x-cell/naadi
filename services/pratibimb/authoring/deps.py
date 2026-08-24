"""FastAPI dependency wiring for authoring routes."""
from __future__ import annotations

from typing import Generator

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.constants import AUTHORING_CONSENT_SCOPES
from services.pratibimb.authoring.db import (
    get_authoring_engine,
    init_authoring_schema,
    reset_authoring_engine_cache,
)
from services.pratibimb.authoring.state_machine import TransitionActor
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.ledger_read.consent_startup import consent_store_backend
from services.pratibimb.ledger_read.consent_store import ConsentGrantStore
from services.pratibimb.ledger_read.deps import (
    _store_for_session,
    get_auth_context,
    get_in_memory_consent_store,
)
from shared.schemas.ledger_read import ConsentScope

_SessionLocal: sessionmaker | None = None


def reset_authoring_wiring_cache() -> None:
    global _SessionLocal
    _SessionLocal = None
    reset_authoring_engine_cache()


def _get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_authoring_engine()
        init_authoring_schema(engine)
        _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _SessionLocal


def authoring_db() -> Generator[Session, None, None]:
    session = _get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def get_authoring_store(session: Session = Depends(authoring_db)) -> CaseDraftStore:
    return CaseDraftStore(session)


def get_consent_store_for_authoring(
    session: Session = Depends(authoring_db),
) -> ConsentGrantStore:
    if consent_store_backend() == "postgres":
        return _store_for_session(session)
    return get_in_memory_consent_store()


async def resolve_authoring_scopes(
    auth: AuthContext,
    store: ConsentGrantStore,
) -> frozenset[str]:
    scopes: set[str] = set()
    for scope in AUTHORING_CONSENT_SCOPES:
        if await store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
        ):
            scopes.add(scope.value)
    return frozenset(scopes)


async def get_authoring_actor(
    auth: AuthContext = Depends(get_auth_context),
    consent_store: ConsentGrantStore = Depends(get_consent_store_for_authoring),
) -> TransitionActor:
    scopes = await resolve_authoring_scopes(auth, consent_store)
    return TransitionActor(subject_id=auth.subject_pseudo_id, scopes=scopes)


async def seed_authoring_scope(
    *,
    tenant_id: str,
    subject_id: str,
    scope: ConsentScope,
    store: ConsentGrantStore | None = None,
) -> None:
    """Test helper — issue a tenant-scoped authoring consent grant."""
    from datetime import datetime, timezone

    from shared.schemas.consent import ConsentGrantSource

    consent = store or get_in_memory_consent_store()
    await consent.issue_grant(
        tenant_id=tenant_id,
        subject_id=subject_id,
        scope=scope,
        resource_id="*",
        granted_by="test",
        source=ConsentGrantSource.INSTITUTIONAL_ADMIN,
        granted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
