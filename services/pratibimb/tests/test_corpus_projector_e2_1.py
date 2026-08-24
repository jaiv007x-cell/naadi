"""Phase E2.1: runtime.published_case_corpus projector — acceptance A1–A10 + pins."""
from __future__ import annotations

import inspect
import json
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.models import (
    CaseDraftRow,
    GoldenFixtureRow,
    PublishedCaseVersionRow,
)
from services.pratibimb.ledger.corpus_projector import (
    CorpusProjector,
    canonical_envelope_dumps,
)
from services.pratibimb.ledger.models import Base, PublishedCaseCorpusRow, RUNTIME_SCHEMA

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
CASE_ID = "anaphylaxis_01"
CASE_VERSION = "v2.0"
CONTENT_HASH = "a" * 64


@pytest.fixture
def authoring_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_authoring_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def corpus_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def projector(authoring_session, corpus_session):
    return CorpusProjector(authoring_session, corpus_session)


def _seed_published(
    authoring_session,
    *,
    tenant_id: str = TENANT_A,
    case_id: str = CASE_ID,
    version: str = CASE_VERSION,
    content_hash: str = CONTENT_HASH,
    retired: bool = False,
    blueprint_json: dict | None = None,
    with_fixture: bool = True,
) -> PublishedCaseVersionRow:
    bp = blueprint_json or {
        "identity": {"case_id": case_id, "version": version, "corpus_tier": "silver"},
        "assessment_mode": "formative",
    }
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        author_subject_id="author-1",
        blueprint_json=bp,
        blueprint_version=version,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        current_state="PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    authoring_session.add(draft)
    authoring_session.flush()
    if with_fixture:
        authoring_session.add(
            GoldenFixtureRow(
                id=str(uuid.uuid4()),
                draft_id=draft.id,
                fixture_kind="perfect_path",
                trace_json={"turns": [{"t": 1}]},
                expected_grade_json={"passed": True},
                content_hash="f" * 64,
            )
        )
        authoring_session.flush()
    now = datetime.now(timezone.utc)
    row = PublishedCaseVersionRow(
        id=str(uuid.uuid4()),
        draft_id=draft.id,
        tenant_id=tenant_id,
        case_id=case_id,
        version=version,
        assessment_mode="formative",
        content_hash=content_hash,
        harness_version=HARNESS_VERSION,
        published_at=now,
        published_by="publisher-5",
        retired_at=now if retired else None,
        retired_by="retirer-6" if retired else None,
        retired_reason_code="policy_change" if retired else None,
    )
    authoring_session.add(row)
    authoring_session.flush()
    return row


# --- A1 / A2 scaffolding -------------------------------------------------


def test_a1_migration_shape_unique_and_shared_pk(corpus_session):
    table = PublishedCaseCorpusRow.__table__
    assert table.schema == RUNTIME_SCHEMA
    assert table.name == "published_case_corpus"
    col_names = {c.name for c in table.columns}
    assert "source_published_id" not in col_names
    assert col_names >= {
        "id",
        "tenant_id",
        "case_id",
        "version",
        "content_hash",
        "assessment_mode",
        "workflow_state",
        "probe_only",
        "harness_version",
        "published_at",
        "envelope_json",
        "projected_at",
    }
    uq_names = {uq.name for uq in table.constraints if getattr(uq, "name", None)}
    assert "uq_runtime_published_case_corpus_slot" in uq_names


def test_a2_orm_roundtrip_envelope_text(corpus_session):
    now = datetime.now(timezone.utc)
    envelope = canonical_envelope_dumps(
        {"blueprint": {"x": 1}, "fixtures": {}, "workflow_state": "PUBLISHED"}
    )
    row = PublishedCaseCorpusRow(
        id="pub-1",
        tenant_id=TENANT_A,
        case_id=CASE_ID,
        version=CASE_VERSION,
        content_hash=CONTENT_HASH,
        assessment_mode="formative",
        workflow_state="PUBLISHED",
        probe_only=False,
        harness_version=HARNESS_VERSION,
        published_at=now,
        envelope_json=envelope,
        projected_at=now,
    )
    corpus_session.add(row)
    corpus_session.commit()
    got = corpus_session.get(PublishedCaseCorpusRow, "pub-1")
    assert got is not None
    assert got.envelope_json == envelope
    assert isinstance(got.envelope_json, str)


# --- Pin 1: probe_only derived only --------------------------------------


def test_pin1_project_one_has_no_probe_only_parameter():
    sig = inspect.signature(CorpusProjector.project_one)
    assert "probe_only" not in sig.parameters
    assert list(sig.parameters) == ["self", "published_id"]


def test_a3_project_one_happy_path(projector, authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    row = projector.project_one(pub.id)
    corpus_session.commit()

    assert row.id == pub.id
    assert row.content_hash == CONTENT_HASH
    assert row.content_hash == pub.content_hash
    assert row.probe_only is False
    assert row.workflow_state == "PUBLISHED"
    assert row.tenant_id == TENANT_A
    env = json.loads(row.envelope_json)
    assert env["blueprint"]["identity"]["case_id"] == CASE_ID
    assert "perfect_path" in env["fixtures"]


def test_a5_retire_derives_probe_only(projector, authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    projector.project_one(pub.id)
    corpus_session.commit()

    pub.retired_at = datetime.now(timezone.utc)
    pub.retired_by = "retirer-6"
    pub.retired_reason_code = "guideline_change"
    authoring_session.flush()

    row = projector.project_one(pub.id)
    corpus_session.commit()

    assert row.id == pub.id
    assert row.content_hash == CONTENT_HASH
    assert row.probe_only is True
    assert row.workflow_state == "RETIRED"
    assert json.loads(row.envelope_json)["workflow_state"] == "RETIRED"


def test_a5_seed_already_retired_projects_probe_only(projector, authoring_session):
    pub = _seed_published(authoring_session, retired=True)
    row = projector.project_one(pub.id)
    assert row.probe_only is True
    assert row.workflow_state == "RETIRED"


# --- A4 envelope freeze --------------------------------------------------


def test_a4_draft_mutate_after_project_leaves_corpus_frozen(
    projector, authoring_session, corpus_session
):
    pub = _seed_published(authoring_session)
    row = projector.project_one(pub.id)
    corpus_session.commit()
    frozen = row.envelope_json

    draft = authoring_session.get(CaseDraftRow, pub.draft_id)
    assert draft is not None
    draft.blueprint_json = {
        **draft.blueprint_json,
        "identity": {
            **draft.blueprint_json["identity"],
            "tampered": True,
        },
    }
    authoring_session.flush()

    reread = corpus_session.get(PublishedCaseCorpusRow, pub.id)
    assert reread is not None
    assert reread.envelope_json == frozen
    assert "tampered" not in json.loads(reread.envelope_json)["blueprint"]["identity"]


# --- A6 / A7 refresh -----------------------------------------------------


def test_a6_refresh_now_idempotent(projector, authoring_session, corpus_session):
    _seed_published(authoring_session, version="v1")
    _seed_published(authoring_session, version="v2", content_hash="b" * 64)
    n1 = projector.refresh_now(TENANT_A)
    corpus_session.commit()
    assert n1 == 2
    hashes_1 = {
        r.envelope_json
        for r in corpus_session.scalars(select(PublishedCaseCorpusRow)).all()
    }

    n2 = projector.refresh_now(TENANT_A)
    corpus_session.commit()
    assert n2 == 2
    rows = list(corpus_session.scalars(select(PublishedCaseCorpusRow)).all())
    assert len(rows) == 2
    hashes_2 = {r.envelope_json for r in rows}
    assert hashes_2 == hashes_1


def test_a7_tenant_isolation(projector, authoring_session, corpus_session):
    _seed_published(authoring_session, tenant_id=TENANT_A)
    _seed_published(
        authoring_session,
        tenant_id=TENANT_B,
        version="v9",
        content_hash="c" * 64,
    )
    projector.refresh_now(TENANT_A)
    corpus_session.commit()

    rows = list(corpus_session.scalars(select(PublishedCaseCorpusRow)).all())
    assert len(rows) == 1
    assert rows[0].tenant_id == TENANT_A


# --- A8 / Pin 2 byte-idempotent ------------------------------------------


def test_a8_reproject_no_second_row_bumps_projected_at(
    projector, authoring_session, corpus_session
):
    pub = _seed_published(authoring_session)
    row1 = projector.project_one(pub.id)
    corpus_session.commit()
    t1 = row1.projected_at
    env1 = row1.envelope_json

    time.sleep(0.01)
    row2 = projector.project_one(pub.id)
    corpus_session.commit()

    assert row2.id == row1.id
    count = len(list(corpus_session.scalars(select(PublishedCaseCorpusRow)).all()))
    assert count == 1
    assert row2.projected_at >= t1
    # Pin 2: raw JSON string equality — not parsed-dict equality
    assert row2.envelope_json == env1
    assert isinstance(row2.envelope_json, str)


def test_pin2_byte_idempotent_canonical_dumps_stable():
    envelope = {
        "workflow_state": "PUBLISHED",
        "blueprint": {"b": 2, "a": 1},
        "fixtures": {},
        "approver_subject_ids": [],
        "harness_version": HARNESS_VERSION,
        "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    assert canonical_envelope_dumps(envelope) == canonical_envelope_dumps(envelope)
    assert canonical_envelope_dumps(envelope) == (
        '{"approver_subject_ids":[],"blueprint":{"a":1,"b":2},'
        '"fixtures":{},"harness_version":"0.1.0",'
        '"published_at":"2026-01-01T00:00:00+00:00","workflow_state":"PUBLISHED"}'
    )


# --- A9 hash copy-only ---------------------------------------------------


def test_a9_content_hash_copied_not_recomputed(
    projector, authoring_session, corpus_session
):
    # Published hash deliberately unrelated to blueprint bytes
    pub = _seed_published(
        authoring_session,
        content_hash="9" * 64,
        blueprint_json={"identity": {"case_id": CASE_ID}, "noise": "x"},
    )
    row = projector.project_one(pub.id)
    assert row.content_hash == "9" * 64
    # Envelope carries blueprint; hash field must still be the published copy
    assert json.loads(row.envelope_json)["blueprint"]["noise"] == "x"
    assert row.content_hash != canonical_envelope_dumps(
        json.loads(row.envelope_json)
    )


# --- A10 own-session / not inside publish txn ----------------------------


def test_a10_projector_uses_separate_corpus_session(
    authoring_session, corpus_session, projector
):
    assert projector._authoring is authoring_session
    assert projector._corpus is corpus_session
    assert authoring_session.bind is not corpus_session.bind

    pub = _seed_published(authoring_session)
    authoring_session.commit()  # simulate publish commit first
    # Projection after authoring commit — corpus write is independent
    row = projector.project_one(pub.id)
    corpus_session.commit()
    assert corpus_session.get(PublishedCaseCorpusRow, pub.id) is not None
    assert row.id == pub.id
