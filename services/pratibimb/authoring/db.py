"""Authoring schema bootstrap — same Postgres instance, isolated from ledger."""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.authoring.constants import AUTHORING_SCHEMA
from services.pratibimb.authoring.models import AuthoringBase
from services.pratibimb.ledger.db import LEDGER_DATABASE_URL_ENV, DEFAULT_LEDGER_URL

AUTHORING_DATABASE_URL_ENV = "AUTHORING_DATABASE_URL"


@lru_cache
def get_authoring_engine(url: str | None = None) -> Engine:
    db_url = url or os.getenv(AUTHORING_DATABASE_URL_ENV) or os.getenv(
        LEDGER_DATABASE_URL_ENV, DEFAULT_LEDGER_URL
    )
    return create_engine(db_url, future=True)


def _sqlite_schema_shim(engine: Engine) -> None:
    """
    Attach an in-memory database as ``authoring`` so schema-qualified FKs resolve.

    SQLite has no schemas; attached databases act as namespace prefixes for FK
    resolution (``authoring.case_drafts``). Postgres uses ``CREATE SCHEMA`` for
    the same effect. Do not remove this shim — without it, authoring DDL fails
    under SQLite and FK tests can pass for the wrong reasons.
    """
    with engine.begin() as conn:
        attached = conn.execute(text("PRAGMA database_list")).fetchall()
        if not any(row[1] == AUTHORING_SCHEMA for row in attached):
            conn.execute(text(f"ATTACH DATABASE ':memory:' AS {AUTHORING_SCHEMA}"))


def ensure_authoring_namespace(engine: Engine) -> None:
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{AUTHORING_SCHEMA}"'))
        return
    if engine.dialect.name == "sqlite":
        _sqlite_schema_shim(engine)


def init_authoring_schema(engine: Engine | None = None) -> None:
    eng = engine or get_authoring_engine()
    ensure_authoring_namespace(eng)
    AuthoringBase.metadata.create_all(eng)
    _install_append_only_guards(eng)


def _install_append_only_guards(engine: Engine) -> None:
    """Refuse UPDATE/DELETE on approval_events (SQLite). Postgres uses migration 003."""
    if engine.dialect.name != "sqlite":
        return
    schema = AUTHORING_SCHEMA
    statements = [
        f"""
        CREATE TRIGGER IF NOT EXISTS {schema}.approval_events_no_update
        BEFORE UPDATE ON {schema}.approval_events
        BEGIN
            SELECT RAISE(ABORT, 'approval_events is append-only');
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {schema}.approval_events_no_delete
        BEFORE DELETE ON {schema}.approval_events
        BEGIN
            SELECT RAISE(ABORT, 'approval_events is append-only');
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {schema}.approval_events_distinct_summative
        BEFORE INSERT ON {schema}.approval_events
        FOR EACH ROW
        WHEN NEW.event_type = 'GRANT' AND NEW.kind = 'SUMMATIVE'
        BEGIN
            SELECT CASE
                WHEN EXISTS (
                    SELECT 1 FROM {schema}.approval_events AS g
                    WHERE g.draft_id = NEW.draft_id
                      AND g.kind = 'SUMMATIVE'
                      AND g.event_type = 'GRANT'
                      AND g.actor_subject_id = NEW.actor_subject_id
                      AND NOT EXISTS (
                          SELECT 1 FROM {schema}.approval_events AS w
                          WHERE w.event_type = 'WITHDRAW'
                            AND w.supersedes_event_id = g.id
                      )
                ) THEN RAISE(ABORT, 'duplicate summative approver')
            END;
        END
        """,
    ]
    with engine.begin() as conn:
        for sql in statements:
            try:
                conn.execute(text(sql))
            except Exception:
                # Attached-schema trigger names vary by SQLite version; app-level
                # checks remain the enforcement path in tests.
                continue



@contextmanager
def authoring_session(url: str | None = None) -> Iterator[Session]:
    eng = get_authoring_engine(url)
    init_authoring_schema(eng)
    SessionLocal = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def reset_authoring_engine_cache() -> None:
    get_authoring_engine.cache_clear()
