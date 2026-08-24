"""
Apply numbered SQL migrations for the authoring schema.

Each schema owns its alembic_version table so rollbacks in authoring never
appear to affect ledger history (and vice versa). This runner is the current
migration tool; Alembic env.py should mirror these settings when adopted:

    context.configure(include_schemas=True, version_table="alembic_version", ...)
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.pratibimb.authoring.constants import AUTHORING_SCHEMA
from services.pratibimb.authoring.db import (
    ensure_authoring_namespace,
    get_authoring_engine,
    init_authoring_schema,
)

_MIGRATION_DIR = Path(__file__).resolve().parent
_MIGRATION_PATTERN = re.compile(r"^(\d+)_.+\.sql$")
_VERSION_TABLE = f'"{AUTHORING_SCHEMA}".alembic_version'


def _migration_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for path in sorted(_MIGRATION_DIR.glob("*.sql")):
        match = _MIGRATION_PATTERN.match(path.name)
        if match:
            files.append((match.group(1).zfill(4), path))
    return files


def _applied_versions(engine: Engine) -> set[str]:
    ensure_authoring_namespace(engine)
    init_authoring_schema(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT version_num FROM {AUTHORING_SCHEMA}.alembic_version")
        ).fetchall()
    return {row[0] for row in rows}


def apply_pending_migrations(engine: Engine | None = None) -> list[str]:
    eng = engine or get_authoring_engine()
    if eng.dialect.name == "postgresql":
        with eng.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{AUTHORING_SCHEMA}"'))
            conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {AUTHORING_SCHEMA}.alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
    else:
        init_authoring_schema(eng)

    applied = _applied_versions(eng)
    newly_applied: list[str] = []

    for version, path in _migration_files():
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        if eng.dialect.name != "postgresql":
            # SQLite tests rely on SQLAlchemy models; skip dialect-specific DDL.
            with eng.begin() as conn:
                conn.execute(
                    text(
                        f"INSERT INTO {AUTHORING_SCHEMA}.alembic_version "
                        "(version_num) VALUES (:version)"
                    ),
                    {"version": version},
                )
            newly_applied.append(version)
            continue

        with eng.begin() as conn:
            conn.execute(text(sql))
            conn.execute(
                text(
                    f"INSERT INTO {AUTHORING_SCHEMA}.alembic_version "
                    "(version_num) VALUES (:version)"
                ),
                {"version": version},
            )
        newly_applied.append(version)

    return newly_applied
