"""E2.2.b: reconcile + decision-idempotent skipped (I-E22-2)."""
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
    set_corpus_projection_stale,
)
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_facade import (
    project_after_authoring_commit,
    reconcile_tenant,
)
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


def _seed_published(
    authoring_session,
    *,
    version: str = "v1",
    content_hash: str = CONTENT_HASH,
) -> PublishedCaseVersionRow:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=TENANT,
        author_subject_id="author-1",
        blueprint_json={"identity": {"case_id": "C1", "version": version}},
        blueprint_version=version,
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
        version=version,
        assessment_mode="formative",
        content_hash=content_hash,
        harness_version=HARNESS_VERSION,
        published_at=now,
        published_by="publisher-5",
    )
    authoring_session.add(row)
    authoring_session.commit()
    return row


def _metric_value(counter, **labels) -> float:
    key = tuple(sorted(labels.items()))
    with counter._lock:
        return float(counter._values.get(key, 0.0))


def test_hook_then_reconcile_one_success_one_skipped(authoring_session, corpus_session):
    """I-E22-2: hook success + immediate reconcile → success+=1, skipped+=1."""
    pub = _seed_published(authoring_session)

    assert (
        project_after_authoring_commit(
            pub.id,
            authoring_session=authoring_session,
            corpus_session=corpus_session,
            tenant_id=TENANT,
            idempotent=False,
        )
        == "success"
    )

    counts = reconcile_tenant(
        TENANT,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    assert counts == {"success": 0, "skipped": 1, "error": 0}
    assert _metric_value(
        AUTHORING_CORPUS_PROJECTION_TOTAL, result="success", tenant=TENANT
    ) == 1.0
    assert _metric_value(
        AUTHORING_CORPUS_PROJECTION_TOTAL, result="skipped", tenant=TENANT
    ) == 1.0


def test_failed_hook_then_reconcile_heals(authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    with patch.object(
        CorpusProjector, "project_one", side_effect=RuntimeError("disk full")
    ):
        assert (
            project_after_authoring_commit(
                pub.id,
                authoring_session=authoring_session,
                corpus_session=corpus_session,
                tenant_id=TENANT,
            )
            == "error"
        )
    assert _metric_value(AUTHORING_CORPUS_PROJECTION_STALE, tenant=TENANT) == 1.0

    counts = reconcile_tenant(
        TENANT,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    assert counts["success"] == 1
    assert counts["error"] == 0
    assert corpus_session.get(PublishedCaseCorpusRow, pub.id) is not None
    assert _metric_value(AUTHORING_CORPUS_PROJECTION_STALE, tenant=TENANT) == 0.0


def test_skipped_is_noop_on_stale_gauge(authoring_session, corpus_session):
    """skipped must not clear stale (spurious stale→clear forbidden)."""
    pub = _seed_published(authoring_session)
    project_after_authoring_commit(
        pub.id,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        tenant_id=TENANT,
    )
    # Simulate unrelated staleness still flagged for the tenant.
    set_corpus_projection_stale(tenant_id=TENANT)
    assert _metric_value(AUTHORING_CORPUS_PROJECTION_STALE, tenant=TENANT) == 1.0

    result = project_after_authoring_commit(
        pub.id,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        tenant_id=TENANT,
        idempotent=True,
    )
    assert result == "skipped"
    assert _metric_value(AUTHORING_CORPUS_PROJECTION_STALE, tenant=TENANT) == 1.0


def test_reconcile_projects_retire_flag_drift(authoring_session, corpus_session):
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

    counts = reconcile_tenant(
        TENANT,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    assert counts["success"] == 1
    assert counts["skipped"] == 0
    row = corpus_session.get(PublishedCaseCorpusRow, pub.id)
    assert row is not None
    assert row.probe_only is True
