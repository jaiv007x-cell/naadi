"""I.b migration 020 belt — CHECK + UNIQUE greps; no verify routes yet."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.i_acceptance

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SQL = (
    _REPO_ROOT
    / "services"
    / "pratibimb"
    / "ledger"
    / "migrations"
    / "020_status_list_identifier_kind.sql"
)


def test_i_b_020_sql_belt_check_and_unique():
    text = _SQL.read_text(encoding="utf-8")
    assert "status_list_identifier" in text
    assert "ck_status_list_identifier_kind" in text
    assert "'credential'" in text and "'regrade'" in text and "'arp'" in text
    assert "PRIMARY KEY (tenant_id, identifier_kind, identifier_id)" in text
    assert "identifier_kind IN ('credential', 'regrade', 'arp')" in text
    # Backfill pin — credential rows non-null kind, not read-path default forever.
    assert "credential_ledger" in text
    assert "'credential'" in text


def test_i_b_020_orm_check_matches_sql():
    from services.pratibimb.ledger.models import StatusListIdentifierRow

    table = StatusListIdentifierRow.__table__
    ck_names = {c.name for c in table.constraints if getattr(c, "name", None)}
    assert "ck_status_list_identifier_kind" in ck_names
    pk = {c.name for c in table.primary_key.columns}
    assert pk == {"tenant_id", "identifier_kind", "identifier_id"}
