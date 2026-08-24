"""Factory for audited ledger read services."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.audit.sink import AuditSink, InMemoryAuditSink, SqlAuditSink
from services.pratibimb.ledger.db import get_audit_engine
from services.pratibimb.ledger.models import Base
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger_read.audit_decorator import AuditingLedgerReadService
from services.pratibimb.ledger_read.catalog_audit import AuditingAuthoringCatalogReadService
from services.pratibimb.ledger_read.catalog_service import AuthoringCatalogReadService
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink
from services.pratibimb.ledger_read.ncvet_audit import AuditingNcvetReadService
from services.pratibimb.ledger_read.ncvet_service import NcvetEvidenceReadService
from services.pratibimb.ledger_read.request_context import get_request_id
from services.pratibimb.ledger_read.service import LedgerReadService
import services.pratibimb.audit.models_audit  # noqa: F401

DEFAULT_FALLBACK_ROOT = "/var/lib/pratibimb/audit-fallback"


def build_fallback_sink(root_dir: str | None = None) -> JsonlFallbackSink:
    return JsonlFallbackSink(
        root_dir or os.environ.get("AUDIT_FALLBACK_DIR", DEFAULT_FALLBACK_ROOT)
    )


@lru_cache(maxsize=1)
def get_fallback_sink_singleton() -> JsonlFallbackSink:
    """
    Process-wide fallback sink shared by safe_emit writes and summative health checks.

    Assumption: one instance per OS process. Under multi-worker deployments (gunicorn,
    uvicorn --workers N), each worker holds its own singleton and its own view of
    fallback health — usually fine because fallback failures are local disk/permission
    issues that affect all workers on the host equally. If health is ever centralized
    (shared drain cron marker file, Redis signal), change only this factory — the
    middleware contract stays the same.
    """
    return build_fallback_sink()


def reset_fallback_sink_cache() -> None:
    get_fallback_sink_singleton.cache_clear()


def build_audited_ledger_read_service(
    reader: SqlLedgerReader,
    *,
    audit_sink: AuditSink | None = None,
    audit_session_factory: Callable[[], Session] | None = None,
    fallback_sink: JsonlFallbackSink | None = None,
    request_id_provider: Callable[[], str | None] | None = None,
) -> AuditingLedgerReadService:
    """
    Wrap LedgerReadService with audit emission.

    Uses a separate session factory for audit writes when audit_sink is not
    provided — isolated from the reader's session so poisoned read transactions
    cannot block audit inserts.
    """
    inner = LedgerReadService(reader)
    if audit_sink is None:
        if audit_session_factory is not None:
            sink: AuditSink = SqlAuditSink(audit_session_factory)
        else:
            engine = get_audit_engine()
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
            sink = SqlAuditSink(factory)
    else:
        sink = audit_sink
    return AuditingLedgerReadService(
        inner,
        sink,
        fallback_sink=fallback_sink or get_fallback_sink_singleton(),
        request_id_provider=request_id_provider or get_request_id,
    )


def build_audited_catalog_read_service(
    store: AuthoringCatalogReadService,
    *,
    audit_sink: AuditSink | None = None,
    audit_session_factory: Callable[[], Session] | None = None,
    fallback_sink: JsonlFallbackSink | None = None,
    request_id_provider: Callable[[], str | None] | None = None,
) -> AuditingAuthoringCatalogReadService:
    inner = store
    if audit_sink is None:
        if audit_session_factory is not None:
            sink: AuditSink = SqlAuditSink(audit_session_factory)
        else:
            engine = get_audit_engine()
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
            sink = SqlAuditSink(factory)
    else:
        sink = audit_sink
    return AuditingAuthoringCatalogReadService(
        inner,
        sink,
        fallback_sink=fallback_sink or get_fallback_sink_singleton(),
        request_id_provider=request_id_provider or get_request_id,
    )


def build_audited_ncvet_read_service(
    store: NcvetEvidenceReadService,
    *,
    audit_sink: AuditSink | None = None,
    audit_session_factory: Callable[[], Session] | None = None,
    fallback_sink: JsonlFallbackSink | None = None,
    request_id_provider: Callable[[], str | None] | None = None,
) -> AuditingNcvetReadService:
    inner = store
    if audit_sink is None:
        if audit_session_factory is not None:
            sink: AuditSink = SqlAuditSink(audit_session_factory)
        else:
            engine = get_audit_engine()
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
            sink = SqlAuditSink(factory)
    else:
        sink = audit_sink
    return AuditingNcvetReadService(
        inner,
        sink,
        fallback_sink=fallback_sink or get_fallback_sink_singleton(),
        request_id_provider=request_id_provider or get_request_id,
    )


def build_audit_session_factory() -> Callable[[], Session]:
    engine = get_audit_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
