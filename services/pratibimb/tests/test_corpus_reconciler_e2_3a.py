"""E2.3.a: reconciler lag (oldest-stale) + reason/outcome row counters (no scheduler)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.metrics import (
    CORPUS_RECONCILER_LAG_SECONDS,
    CORPUS_RECONCILER_ROW_TOTAL,
    record_corpus_reconciler_row,
    reset_authoring_metrics_for_tests,
)
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_facade import (
    compute_reconciler_lag_seconds,
    project_after_authoring_commit,
    reconcile_tenant,
)
from services.pratibimb.ledger.models import Base, PublishedCaseCorpusRow

TENANT = "tenant-a"
CONTENT_HASH = "a" * 64
NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


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


def _metric_value(counter, **labels) -> float:
    key = tuple(sorted(labels.items()))
    with counter._lock:
        return float(counter._values.get(key, 0.0))


def _seed_published(
    authoring_session,
    *,
    version: str = "v1",
    case_id: str = "C1",
    content_hash: str = CONTENT_HASH,
    published_at: datetime | None = None,
) -> PublishedCaseVersionRow:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=TENANT,
        author_subject_id="author-1",
        blueprint_json={"identity": {"case_id": case_id, "version": version}},
        blueprint_version=version,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        current_state="PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    authoring_session.add(draft)
    authoring_session.flush()
    row = PublishedCaseVersionRow(
        id=str(uuid.uuid4()),
        draft_id=draft.id,
        tenant_id=TENANT,
        case_id=case_id,
        version=version,
        assessment_mode="formative",
        content_hash=content_hash,
        harness_version=HARNESS_VERSION,
        published_at=published_at or NOW,
        published_by="publisher-5",
    )
    authoring_session.add(row)
    authoring_session.commit()
    return row


def _corpus_stub(
    published: PublishedCaseVersionRow,
    *,
    probe_only: bool = False,
    workflow_state: str = "PUBLISHED",
) -> PublishedCaseCorpusRow:
    return PublishedCaseCorpusRow(
        id=published.id,
        tenant_id=published.tenant_id,
        case_id=published.case_id,
        version=published.version,
        content_hash=published.content_hash,
        assessment_mode=published.assessment_mode,
        workflow_state=workflow_state,
        probe_only=probe_only,
        harness_version=published.harness_version,
        published_at=published.published_at,
        envelope_json="{}",
        projected_at=NOW,
    )


# --- lag formula (pure) ---


def test_lag_zero_when_no_published_rows():
    assert compute_reconciler_lag_seconds([], {}, now=NOW) == 0.0


def test_lag_zero_when_all_rows_current(authoring_session):
    pub = _seed_published(authoring_session, published_at=NOW - timedelta(hours=1))
    corpus = _corpus_stub(pub)
    assert (
        compute_reconciler_lag_seconds([pub], {pub.id: corpus}, now=NOW) == 0.0
    )


def test_lag_reports_oldest_stale_row_age(authoring_session):
    """Oldest-stale: lag = now - min(published_at) over stale, not newest stale."""
    old = _seed_published(
        authoring_session,
        version="v-old",
        case_id="OLD",
        published_at=NOW - timedelta(seconds=300),
    )
    new = _seed_published(
        authoring_session,
        version="v-new",
        case_id="NEW",
        published_at=NOW - timedelta(seconds=60),
    )
    lag = compute_reconciler_lag_seconds(
        [old, new],
        {old.id: None, new.id: None},
        now=NOW,
    )
    assert lag == 300.0


def test_lag_missing_row_counts_as_stale(authoring_session):
    pub = _seed_published(
        authoring_session, published_at=NOW - timedelta(seconds=120)
    )
    lag = compute_reconciler_lag_seconds([pub], {pub.id: None}, now=NOW)
    assert lag == 120.0


def test_lag_retire_flag_drift_counts_as_stale(authoring_session):
    pub = _seed_published(
        authoring_session, published_at=NOW - timedelta(seconds=90)
    )
    pub.retired_at = NOW
    pub.retired_by = "retirer-6"
    pub.retired_reason_code = "guideline_change"
    authoring_session.commit()
    # Corpus still shows active PUBLISHED — drift.
    stale_corpus = _corpus_stub(pub, probe_only=False, workflow_state="PUBLISHED")
    lag = compute_reconciler_lag_seconds(
        [pub], {pub.id: stale_corpus}, now=NOW
    )
    assert lag == 90.0


def test_lag_ignores_current_rows_when_computing_min(authoring_session):
    """min() is over the stale set only — an old current row must not inflate lag."""
    current_old = _seed_published(
        authoring_session,
        version="v-current",
        case_id="CUR",
        published_at=NOW - timedelta(seconds=1000),
    )
    stale_newer = _seed_published(
        authoring_session,
        version="v-stale",
        case_id="STALE",
        published_at=NOW - timedelta(seconds=45),
    )
    lag = compute_reconciler_lag_seconds(
        [current_old, stale_newer],
        {
            current_old.id: _corpus_stub(current_old),
            stale_newer.id: None,
        },
        now=NOW,
    )
    assert lag == 45.0


# --- label guards ---


def test_record_rejects_unknown_reason():
    with pytest.raises(ValueError, match="invalid reconciler reason"):
        record_corpus_reconciler_row(
            tenant_id=TENANT, reason="peridoic", outcome="success"
        )


def test_record_rejects_unknown_outcome():
    with pytest.raises(ValueError, match="invalid reconciler outcome"):
        record_corpus_reconciler_row(
            tenant_id=TENANT, reason="manual", outcome="projected"
        )


# --- reconcile_tenant metrics ---


def test_reconcile_tenant_default_reason_is_manual(authoring_session, corpus_session):
    _seed_published(authoring_session)
    counts = reconcile_tenant(
        TENANT,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    assert counts["success"] == 1
    assert (
        _metric_value(
            CORPUS_RECONCILER_ROW_TOTAL,
            tenant=TENANT,
            reason="manual",
            outcome="success",
        )
        == 1.0
    )


def test_reconcile_records_outcome_skipped_when_current(
    authoring_session, corpus_session
):
    pub = _seed_published(authoring_session)
    project_after_authoring_commit(
        pub.id,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        tenant_id=TENANT,
    )
    counts = reconcile_tenant(
        TENANT,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        reason="manual",
        now=NOW,
    )
    assert counts == {"success": 0, "skipped": 1, "error": 0}
    assert (
        _metric_value(
            CORPUS_RECONCILER_ROW_TOTAL,
            tenant=TENANT,
            reason="manual",
            outcome="skipped",
        )
        == 1.0
    )
    assert _metric_value(CORPUS_RECONCILER_LAG_SECONDS, tenant=TENANT) == 0.0


def test_reconcile_records_outcome_success_and_clears_lag(
    authoring_session, corpus_session
):
    pub = _seed_published(
        authoring_session, published_at=NOW - timedelta(seconds=200)
    )
    pre = compute_reconciler_lag_seconds([pub], {pub.id: None}, now=NOW)
    assert pre == 200.0

    counts = reconcile_tenant(
        TENANT,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        reason="manual",
        now=NOW,
    )
    assert counts["success"] == 1
    assert (
        _metric_value(
            CORPUS_RECONCILER_ROW_TOTAL,
            tenant=TENANT,
            reason="manual",
            outcome="success",
        )
        == 1.0
    )
    assert _metric_value(CORPUS_RECONCILER_LAG_SECONDS, tenant=TENANT) == 0.0


def test_reconcile_does_not_emit_startup_or_periodic_without_explicit_reason(
    authoring_session, corpus_session
):
    _seed_published(authoring_session)
    reconcile_tenant(
        TENANT,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
    )
    for reason in ("startup", "periodic"):
        for outcome in ("success", "skipped", "error"):
            assert (
                _metric_value(
                    CORPUS_RECONCILER_ROW_TOTAL,
                    tenant=TENANT,
                    reason=reason,
                    outcome=outcome,
                )
                == 0.0
            )


def test_reconcile_records_outcome_error(authoring_session, corpus_session):
    from services.pratibimb.ledger.corpus_projector import CorpusProjector

    _seed_published(
        authoring_session, published_at=NOW - timedelta(seconds=75)
    )
    with patch.object(
        CorpusProjector, "project_one", side_effect=RuntimeError("disk full")
    ):
        counts = reconcile_tenant(
            TENANT,
            authoring_session=authoring_session,
            corpus_session=corpus_session,
            reason="manual",
            now=NOW,
        )
    assert counts["error"] == 1
    assert (
        _metric_value(
            CORPUS_RECONCILER_ROW_TOTAL,
            tenant=TENANT,
            reason="manual",
            outcome="error",
        )
        == 1.0
    )
    assert _metric_value(CORPUS_RECONCILER_LAG_SECONDS, tenant=TENANT) == 75.0


def test_explicit_reason_startup_label(authoring_session, corpus_session):
    _seed_published(authoring_session)
    reconcile_tenant(
        TENANT,
        authoring_session=authoring_session,
        corpus_session=corpus_session,
        reason="startup",
    )
    assert (
        _metric_value(
            CORPUS_RECONCILER_ROW_TOTAL,
            tenant=TENANT,
            reason="startup",
            outcome="success",
        )
        == 1.0
    )
    assert (
        _metric_value(
            CORPUS_RECONCILER_ROW_TOTAL,
            tenant=TENANT,
            reason="manual",
            outcome="success",
        )
        == 0.0
    )
