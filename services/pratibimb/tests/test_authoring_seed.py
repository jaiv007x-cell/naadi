"""Blueprint JSON round-trip and Ramesh seed tests."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.migrate_cases_v1_to_v2 import migrate_case_v1_to_v2
from services.pratibimb.authoring.blueprint_io import (
    blueprint_from_case_json,
    content_hash_from_case_json,
)
from services.pratibimb.authoring.constants import RAMESH_CASE_ID, RAMESH_DRAFT_ID
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.seed import seed_ramesh_draft
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.tests.test_case_migration import _v1


def test_blueprint_json_round_trip_preserves_content_hash():
    result = migrate_case_v1_to_v2(_v1())
    assert result.blueprint is not None
    payload = result.blueprint.to_dict()
    reloaded = blueprint_from_case_json(payload)
    assert reloaded.compute_content_hash() == result.blueprint.compute_content_hash()
    assert content_hash_from_case_json(payload) == result.blueprint.compute_content_hash()


def test_draft_save_reload_preserves_content_hash(seeded_session):
    """ORM JSON round-trip must not drift content_hash — load-bearing for publish immutability."""
    store = CaseDraftStore(seeded_session)
    result = migrate_case_v1_to_v2(_v1())
    assert result.blueprint is not None
    payload = result.blueprint.to_dict()
    expected_hash = content_hash_from_case_json(payload)

    draft = store.create_draft(
        tenant_id="tenant-a",
        author_subject_id="author-1",
        blueprint_json=payload,
        blueprint_version=result.blueprint.version,
    )
    seeded_session.commit()
    seeded_session.expire_all()

    reloaded = store.get_draft(draft.id)
    assert reloaded is not None
    assert content_hash_from_case_json(reloaded.blueprint_json) == expected_hash


@pytest.fixture
def seeded_session():
    # See authoring.db._sqlite_schema_shim — ATTACH is required for schema FK parity.
    engine = create_engine("sqlite:///:memory:", future=True)
    init_authoring_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    seed_ramesh_draft(session)
    yield session
    session.close()


def test_ramesh_seed_is_idempotent(seeded_session):
    again = seed_ramesh_draft(seeded_session)
    assert again is None
    store = CaseDraftStore(seeded_session)
    draft = store.get_draft(RAMESH_DRAFT_ID)
    assert draft is not None
    assert draft.blueprint_json["identity"]["case_id"] == RAMESH_CASE_ID
