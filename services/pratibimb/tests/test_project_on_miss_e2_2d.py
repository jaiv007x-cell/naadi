"""E2.2.d: project_on_miss — sole sync runtime→projector heal (I-E22-5)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.session.manager import SessionManager
from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.errors import PublishedCaseNotFoundError
from services.pratibimb.authoring.metrics import (
    AUTHORING_CORPUS_PROJECT_ON_MISS_TOTAL,
    reset_authoring_metrics_for_tests,
)
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_projector import CorpusProjector
from services.pratibimb.ledger.models import Base, PublishedCaseCorpusRow
from shared.schemas.session import CreateSessionRequest

TENANT = "tenant-a"
CASE_ID = "C1"
VERSION = "v1"
HASH = "a" * 64


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
    retired: bool = False,
    version: str = VERSION,
    content_hash: str = HASH,
) -> PublishedCaseVersionRow:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=TENANT,
        author_subject_id="author-1",
        blueprint_json={"identity": {"case_id": CASE_ID, "version": version}},
        blueprint_version=version,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        current_state="RETIRED" if retired else "PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    authoring_session.add(draft)
    authoring_session.flush()
    now = datetime.now(timezone.utc)
    row = PublishedCaseVersionRow(
        id=str(uuid.uuid4()),
        draft_id=draft.id,
        tenant_id=TENANT,
        case_id=CASE_ID,
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
    authoring_session.commit()
    return row


def _metric(outcome: str, tenant: str = TENANT) -> float:
    key = tuple(sorted({"outcome": outcome, "tenant": tenant}.items()))
    with AUTHORING_CORPUS_PROJECT_ON_MISS_TOTAL._lock:
        return float(AUTHORING_CORPUS_PROJECT_ON_MISS_TOTAL._values.get(key, 0.0))


def test_miss_authoring_exists_projects_and_increments(authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    projector = CorpusProjector(authoring_session, corpus_session)
    assert (
        projector.get_by_triple(tenant_id=TENANT, case_id=CASE_ID, version=VERSION)
        is None
    )

    row = projector.project_on_miss(
        tenant_id=TENANT, case_id=CASE_ID, version=VERSION
    )
    assert row is not None
    assert row.id == pub.id
    assert row.content_hash == HASH
    assert corpus_session.get(PublishedCaseCorpusRow, pub.id) is not None
    assert _metric("projected") == 1.0
    assert _metric("not_found") == 0.0


def test_miss_authoring_missing_returns_none(authoring_session, corpus_session):
    projector = CorpusProjector(authoring_session, corpus_session)
    row = projector.project_on_miss(
        tenant_id=TENANT, case_id=CASE_ID, version=VERSION
    )
    assert row is None
    assert _metric("not_found") == 1.0
    assert _metric("projected") == 0.0


def test_corpus_hit_never_invokes_project_on_miss(authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    CorpusProjector(authoring_session, corpus_session).project_one(pub.id)
    corpus_session.commit()
    reset_authoring_metrics_for_tests()

    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    with patch(
        "services.pratibimb.ledger.corpus_projector.CorpusProjector.project_on_miss"
    ) as mocked_miss:
        resp = mgr.create_session(
            CreateSessionRequest(
                learner_id="L1",
                tenant_id=TENANT,
                case_id=CASE_ID,
                case_version=VERSION,
            )
        )
        mocked_miss.assert_not_called()
        assert mgr._sessions[resp.session_id].blueprint_content_hash == HASH
    assert _metric("projected") == 0.0


def test_create_session_miss_heals_synchronously(authoring_session, corpus_session):
    """D2 race: published, un-projected → create_session heals inside one call."""
    pub = _seed_published(authoring_session)
    assert corpus_session.get(PublishedCaseCorpusRow, pub.id) is None

    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    resp = mgr.create_session(
        CreateSessionRequest(
            learner_id="L1",
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=VERSION,
        )
    )
    assert corpus_session.get(PublishedCaseCorpusRow, pub.id) is not None
    assert mgr._sessions[resp.session_id].blueprint_content_hash == HASH
    assert _metric("projected") == 1.0


def test_create_session_not_found_no_session_stored(authoring_session, corpus_session):
    mgr = SessionManager(
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    with pytest.raises(PublishedCaseNotFoundError):
        mgr.create_session(
            CreateSessionRequest(
                learner_id="L1",
                tenant_id=TENANT,
                case_id=CASE_ID,
                case_version=VERSION,
            )
        )
    assert mgr._sessions == {}
    assert _metric("not_found") == 1.0


def test_concurrent_miss_same_key_no_duplicate_error(authoring_session, corpus_session):
    pub = _seed_published(authoring_session)
    p1 = CorpusProjector(authoring_session, corpus_session)
    p2 = CorpusProjector(authoring_session, corpus_session)
    r1 = p1.project_on_miss(tenant_id=TENANT, case_id=CASE_ID, version=VERSION)
    r2 = p2.project_on_miss(tenant_id=TENANT, case_id=CASE_ID, version=VERSION)
    assert r1 is not None and r2 is not None
    assert r1.id == r2.id == pub.id
    rows = list(corpus_session.scalars(select(PublishedCaseCorpusRow)))
    assert len(rows) == 1
    assert _metric("projected") == 2.0


def test_retired_authoring_row_still_projects(authoring_session, corpus_session):
    pub = _seed_published(authoring_session, retired=True)
    projector = CorpusProjector(authoring_session, corpus_session)
    row = projector.project_on_miss(
        tenant_id=TENANT, case_id=CASE_ID, version=VERSION
    )
    assert row is not None
    assert row.id == pub.id
    assert row.probe_only is True
    assert row.workflow_state == "RETIRED"
    assert _metric("projected") == 1.0
