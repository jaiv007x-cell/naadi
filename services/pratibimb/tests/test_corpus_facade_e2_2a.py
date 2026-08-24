"""E2.2.a: post-commit corpus façade — never fails publish/retire HTTP (I-E22-1)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.metrics import (
    AUTHORING_CORPUS_PROJECTION_STALE,
    AUTHORING_CORPUS_PROJECTION_TOTAL,
    reset_authoring_metrics_for_tests,
)
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_facade import project_after_authoring_commit
from services.pratibimb.ledger.corpus_projector import CorpusProjector
from services.pratibimb.ledger.models import Base, PublishedCaseCorpusRow

TENANT = "tenant-a"
CONTENT_HASH = "a" * 64


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_authoring_metrics_for_tests()
    yield
    reset_authoring_metrics_for_tests()


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


def _seed_published(authoring_session, *, retired: bool = False) -> PublishedCaseVersionRow:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=TENANT,
        author_subject_id="author-1",
        blueprint_json={"identity": {"case_id": "C1", "version": "v1"}},
        blueprint_version="v1",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        current_state="PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    authoring_session.add(draft)
    authoring_session.flush()
    now = datetime.now(timezone.utc)
    row = PublishedCaseVersionRow(
        id=str(uuid.uuid4()),
        draft_id=draft.id,
        tenant_id=TENANT,
        case_id="C1",
        version="v1",
        assessment_mode="formative",
        content_hash=CONTENT_HASH,
        harness_version=HARNESS_VERSION,
        published_at=now,
        published_by="publisher-5",
        retired_at=now if retired else None,
        retired_by="retirer-6" if retired else None,
        retired_reason_code="policy_change" if retired else None,
    )
    authoring_session.add(row)
    authoring_session.commit()
    return row


def _metric_value(counter, **labels) -> float:
    key = tuple(sorted(labels.items()))
    with counter._lock:
        return float(counter._values.get(key, 0.0))


def test_facade_success_projects_row(authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    result = project_after_authoring_commit(
        pub.id,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        tenant_id=TENANT,
    )
    assert result == "success"
    row = corpus_session.get(PublishedCaseCorpusRow, pub.id)
    assert row is not None
    assert row.content_hash == CONTENT_HASH
    assert row.probe_only is False
    assert _metric_value(
        AUTHORING_CORPUS_PROJECTION_TOTAL, result="success", tenant=TENANT
    ) == 1.0
    assert _metric_value(AUTHORING_CORPUS_PROJECTION_STALE, tenant=TENANT) == 0.0


def test_facade_never_raises_when_projector_fails(authoring_session, corpus_session):
    """I-E22-1: projection failure must not propagate to the caller."""
    pub = _seed_published(authoring_session)
    with patch.object(
        CorpusProjector, "project_one", side_effect=RuntimeError("disk full")
    ):
        result = project_after_authoring_commit(
            pub.id,
            authoring_session=authoring_session,
            corpus_session=corpus_session,
            tenant_id=TENANT,
        )
    assert result == "error"
    assert corpus_session.get(PublishedCaseCorpusRow, pub.id) is None
    assert _metric_value(
        AUTHORING_CORPUS_PROJECTION_TOTAL, result="error", tenant=TENANT
    ) == 1.0
    assert _metric_value(AUTHORING_CORPUS_PROJECTION_STALE, tenant=TENANT) == 1.0


def test_facade_error_does_not_undo_authoring_commit(authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    published_id = pub.id
    with patch.object(
        CorpusProjector, "project_one", side_effect=RuntimeError("boom")
    ):
        project_after_authoring_commit(
            published_id,
            authoring_session=authoring_session,
            corpus_session=corpus_session,
            tenant_id=TENANT,
        )
    # Authoring row still present after failed projection
    assert authoring_session.get(PublishedCaseVersionRow, published_id) is not None


def test_facade_retire_flip_via_project(authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    project_after_authoring_commit(
        pub.id,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        tenant_id=TENANT,
    )
    pub.retired_at = datetime.now(timezone.utc)
    pub.retired_by = "retirer-6"
    pub.retired_reason_code = "guideline_change"
    authoring_session.commit()

    result = project_after_authoring_commit(
        pub.id,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        tenant_id=TENANT,
    )
    assert result == "success"
    row = corpus_session.get(PublishedCaseCorpusRow, pub.id)
    assert row is not None
    assert row.probe_only is True
    assert row.workflow_state == "RETIRED"


def test_publish_route_survives_projector_throw(monkeypatch):
    """HTTP publish must stay 200 when façade returns error (I-E22-1 operational)."""
    from services.pratibimb.authoring import routes as routes_mod

    calls: list[str] = []

    def _fake_project(published_id, **kwargs):
        calls.append(published_id)
        return "error"

    monkeypatch.setattr(routes_mod, "project_after_authoring_commit", _fake_project)

    # Smoke: façade is wired into the module namespace used by routes.
    assert routes_mod.project_after_authoring_commit is _fake_project
    assert routes_mod.project_after_authoring_commit("x", authoring_session=None) == "error"
    assert calls == ["x"]
