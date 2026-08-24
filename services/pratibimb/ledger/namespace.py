"""Runtime schema namespace helpers (no ORM imports — safe for models bootstrap)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

RUNTIME_SCHEMA = "runtime"


def _sqlite_runtime_attach_path(engine: Engine) -> str:
    """File-backed attach path shared across connections (F.a / corpus reads)."""
    database = engine.url.database
    if database and database != ":memory:":
        return f"{Path(database).as_posix()}.runtime"
    return ":memory:"


def ensure_runtime_namespace(engine: Engine) -> None:
    """
    Ensure ``runtime`` schema exists (Postgres) or is ATTACHed (SQLite).

    Postgres: ``CREATE SCHEMA IF NOT EXISTS runtime`` — production path.

    SQLite-only workaround (test-pool visibility): file-backed URLs attach
    ``<main>.runtime`` so ``runtime.*`` tables are shared across pooled
    connections. ``ATTACH ':memory:' AS runtime`` is per-connection only and
    breaks HTTP integration tests that seed projection rows on one connection
    and read on another. **Never** apply ATTACH on non-SQLite dialects.
    """
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{RUNTIME_SCHEMA}"'))
        return
    if engine.dialect.name == "sqlite":
        attach_path = _sqlite_runtime_attach_path(engine)
        with engine.begin() as conn:
            attached = conn.execute(text("PRAGMA database_list")).fetchall()
            if not any(row[1] == RUNTIME_SCHEMA for row in attached):
                conn.execute(
                    text(f"ATTACH DATABASE '{attach_path}' AS {RUNTIME_SCHEMA}")
                )
