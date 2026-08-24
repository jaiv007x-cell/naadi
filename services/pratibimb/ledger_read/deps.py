"""
Dependency wiring for ledger_read.

JWT auth: TenantJwks registry → JwksCache → JwtVerifier → ConsentGrantStore.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.auth.context import AuthContext, JwtAuthContextProvider
from services.pratibimb.auth.jwks_cache import JwksCache
from services.pratibimb.auth.jwt_verifier import JwtVerifier
from services.pratibimb.auth.startup import dev_header_allowed
from services.pratibimb.audit.sink import InMemoryAuditSink, SqlAuditSink
from services.pratibimb.ledger.db import get_engine, ledger_session
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.consent_startup import consent_store_backend
from services.pratibimb.ledger_read.consent_store import (
    ConsentGrantStore,
    InMemoryConsentGrantStore,
    PostgresConsentGrantStore,
)
from services.pratibimb.ledger_read.factory import build_audited_ledger_read_service
from services.pratibimb.ledger_read.gateway import LedgerReadGateway
from services.pratibimb.ledger_read.jwt_gateway import JwtLedgerGateway
from services.pratibimb.ledger_read.privacy import PrivacyPolicy
from services.pratibimb.ledger_read.service import LedgerReadService
from services.pratibimb.auth.tenant_jwks import (
    SqlTenantIssuerRegistry,
    TenantIssuerRegistry,
    TenantJwks,
    registry_from_env,
)
from services.pratibimb.ledger.models import Base
import services.pratibimb.auth.models_registry  # noqa: F401 — register ORM tables
import services.pratibimb.audit.models_audit  # noqa: F401 — register audit ORM

_in_memory_store = InMemoryConsentGrantStore()


def get_in_memory_consent_store() -> InMemoryConsentGrantStore:
    return _in_memory_store


def set_consent_store(store: InMemoryConsentGrantStore) -> None:
    global _in_memory_store
    _in_memory_store = store


def _store_for_session(session: Session) -> ConsentGrantStore:
    if consent_store_backend() == "postgres":
        return PostgresConsentGrantStore(session)
    return get_in_memory_consent_store()


def _tenant_registry_backend() -> str:
    return os.environ.get("TENANT_REGISTRY", "memory").strip().lower()


class _SessionBackedRegistry:
    """Opens a short-lived DB session per resolve (production path)."""

    def __init__(self) -> None:
        engine = get_engine()
        Base.metadata.create_all(engine)
        self._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def resolve(self, tenant_id: str, issuer: str):
        with self._SessionLocal() as session:
            return SqlTenantIssuerRegistry(session).resolve(tenant_id, issuer)


@lru_cache(maxsize=1)
def get_tenant_issuer_registry() -> TenantIssuerRegistry:
    if _tenant_registry_backend() == "postgres":
        return _SessionBackedRegistry()
    return registry_from_env()


@lru_cache(maxsize=1)
def get_jwks_cache() -> JwksCache:
    return JwksCache()


@lru_cache(maxsize=1)
def get_tenant_jwks() -> TenantJwks:
    return TenantJwks(get_tenant_issuer_registry(), get_jwks_cache())


@lru_cache(maxsize=1)
def get_auth_provider() -> JwtAuthContextProvider:
    return JwtAuthContextProvider(
        verifier=JwtVerifier(get_tenant_jwks()),
        allow_dev_header=dev_header_allowed(),
    )


def reset_auth_wiring_cache() -> None:
    get_tenant_issuer_registry.cache_clear()
    get_jwks_cache.cache_clear()
    get_tenant_jwks.cache_clear()
    get_auth_provider.cache_clear()


def get_auth_context(request: Request) -> AuthContext:
    return get_auth_provider().from_request(request)


def ledger_reader() -> Generator[SqlLedgerReader, None, None]:
    with ledger_session() as session:
        yield SqlLedgerReader(session)


def _audit_sink_backend() -> str:
    return os.environ.get("AUDIT_SINK", "memory").strip().lower()


@lru_cache(maxsize=1)
def get_audit_sink():
    if _audit_sink_backend() == "postgres":
        from services.pratibimb.ledger_read.factory import build_audit_session_factory

        return SqlAuditSink(build_audit_session_factory())
    return InMemoryAuditSink()


def get_jwt_gateway(
    reader: SqlLedgerReader = Depends(ledger_reader),
) -> JwtLedgerGateway:
    audited = build_audited_ledger_read_service(
        reader,
        audit_sink=get_audit_sink(),
    )
    consent = ConsentResolver(_store_for_session(reader._s))
    return JwtLedgerGateway(audited, consent, PrivacyPolicy())


def build_gateway(reader: SqlLedgerReader) -> LedgerReadGateway:
    audited = build_audited_ledger_read_service(
        reader,
        audit_sink=get_audit_sink(),
    )
    consent = ConsentResolver(_store_for_session(reader._s))
    return LedgerReadGateway(audited, consent, PrivacyPolicy())
