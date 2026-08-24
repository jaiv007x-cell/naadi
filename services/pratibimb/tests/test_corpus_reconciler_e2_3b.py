"""E2.3.b: periodic reconciler driver — tick_once, discovery, fail-soft, shutdown."""
from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.authoring.constants import HARNESS_VERSION
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.metrics import (
    CORPUS_RECONCILER_PASS_TOTAL,
    CORPUS_RECONCILER_ROW_TOTAL,
    reset_authoring_metrics_for_tests,
)
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_reconciler import (
    CorpusReconcilerDriver,
    list_published_tenant_ids,
    resolve_reconcile_interval_seconds,
)

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
NOW = datetime(2026, 8, 21, 18, 0, 0, tzinfo=timezone.utc)
CONTENT_HASH = "b" * 64


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_authoring_metrics_for_tests()
    yield
    reset_authoring_metrics_for_tests()


@pytest.fixture
def authoring_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_authoring_schema(engine)
    return engine


@pytest.fixture
def session_factory(authoring_engine):
    SessionLocal = sessionmaker(
        bind=authoring_engine, expire_on_commit=False, future=True
    )

    @contextmanager
    def _factory() -> Iterator[Session]:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    return _factory


def _metric_value(counter, **labels) -> float:
    key = tuple(sorted(labels.items()))
    with counter._lock:
        return float(counter._values.get(key, 0.0))


def _seed_published(session: Session, *, tenant_id: str, version: str = "v1") -> None:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        author_subject_id="author-1",
        blueprint_json={"identity": {"case_id": "C1", "version": version}},
        blueprint_version=version,
        created_at=NOW,
        updated_at=NOW,
        current_state="PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    session.add(draft)
    session.flush()
    session.add(
        PublishedCaseVersionRow(
            id=str(uuid.uuid4()),
            draft_id=draft.id,
            tenant_id=tenant_id,
            case_id="C1",
            version=version,
            assessment_mode="formative",
            content_hash=CONTENT_HASH,
            harness_version=HARNESS_VERSION,
            published_at=NOW,
            published_by="publisher-5",
        )
    )
    session.commit()


def test_resolve_interval_default_and_clamp():
    assert resolve_reconcile_interval_seconds(None) == 60.0
    assert resolve_reconcile_interval_seconds(10) == 15.0
    assert resolve_reconcile_interval_seconds(9999) == 600.0
    assert resolve_reconcile_interval_seconds(90) == 90.0


def test_startup_sweep_runs_once_per_tenant(session_factory):
    with session_factory() as s:
        _seed_published(s, tenant_id=TENANT_A)
        _seed_published(s, tenant_id=TENANT_B, version="v2")

    calls: list[tuple[str, str, datetime]] = []

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        calls.append((tenant_id, reason, now))
        return {"success": 0, "skipped": 0, "error": 0}

    driver = CorpusReconcilerDriver(
        authoring_session_factory=session_factory,
        reconcile_fn=_reconcile,
        now_fn=lambda: NOW,
    )
    results = driver.tick_once(reason="startup", now=NOW)
    assert set(results) == {TENANT_A, TENANT_B}
    assert all(v == "ok" for v in results.values())
    assert {(t, r) for t, r, _ in calls} == {
        (TENANT_A, "startup"),
        (TENANT_B, "startup"),
    }
    assert all(n == NOW for _, _, n in calls)
    assert (
        _metric_value(
            CORPUS_RECONCILER_PASS_TOTAL,
            tenant=TENANT_A,
            reason="startup",
            outcome="ok",
        )
        == 1.0
    )


def test_periodic_tick_uses_periodic_reason(session_factory):
    with session_factory() as s:
        _seed_published(s, tenant_id=TENANT_A)

    reasons: list[str] = []

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        reasons.append(reason)
        return {"success": 0, "skipped": 0, "error": 0}

    driver = CorpusReconcilerDriver(
        authoring_session_factory=session_factory,
        reconcile_fn=_reconcile,
    )
    driver.tick_once(reason="periodic", now=NOW)
    assert reasons == ["periodic"]
    assert (
        _metric_value(
            CORPUS_RECONCILER_PASS_TOTAL,
            tenant=TENANT_A,
            reason="periodic",
            outcome="ok",
        )
        == 1.0
    )


def test_tenant_discovery_picks_up_new_tenants(session_factory):
    with session_factory() as s:
        _seed_published(s, tenant_id=TENANT_A)

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        return {"success": 0, "skipped": 0, "error": 0}

    driver = CorpusReconcilerDriver(
        authoring_session_factory=session_factory,
        reconcile_fn=_reconcile,
    )
    first = driver.tick_once(reason="periodic", now=NOW)
    assert set(first) == {TENANT_A}

    with session_factory() as s:
        _seed_published(s, tenant_id=TENANT_B, version="v9")

    second = driver.tick_once(reason="periodic", now=NOW)
    assert set(second) == {TENANT_A, TENANT_B}


def test_one_tenant_failing_does_not_skip_others(session_factory):
    with session_factory() as s:
        _seed_published(s, tenant_id=TENANT_A)
        _seed_published(s, tenant_id=TENANT_B, version="v2")

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        if tenant_id == TENANT_A:
            raise RuntimeError("tenant-a boom")
        return {"success": 0, "skipped": 0, "error": 0}

    driver = CorpusReconcilerDriver(
        authoring_session_factory=session_factory,
        reconcile_fn=_reconcile,
    )
    results = driver.tick_once(reason="periodic", now=NOW)
    assert results[TENANT_A] == "error"
    assert results[TENANT_B] == "ok"
    assert (
        _metric_value(
            CORPUS_RECONCILER_PASS_TOTAL,
            tenant=TENANT_A,
            reason="periodic",
            outcome="error",
        )
        == 1.0
    )
    assert (
        _metric_value(
            CORPUS_RECONCILER_PASS_TOTAL,
            tenant=TENANT_B,
            reason="periodic",
            outcome="ok",
        )
        == 1.0
    )
    # Run-level failure must not invent row_total errors.
    assert (
        _metric_value(
            CORPUS_RECONCILER_ROW_TOTAL,
            tenant=TENANT_A,
            reason="periodic",
            outcome="error",
        )
        == 0.0
    )


@pytest.mark.asyncio
async def test_shutdown_cancels_within_one_tick(session_factory):
    ticks = {"n": 0}

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        ticks["n"] += 1
        return {"success": 0, "skipped": 0, "error": 0}

    def _list(_session):
        return [TENANT_A]

    driver = CorpusReconcilerDriver(
        interval_seconds=3600,
        authoring_session_factory=session_factory,
        reconcile_fn=_reconcile,
        list_tenants_fn=_list,
    )
    task = asyncio.create_task(driver.run())
    # Let startup tick complete.
    for _ in range(50):
        if ticks["n"] >= 1:
            break
        await asyncio.sleep(0.01)
    assert ticks["n"] == 1
    driver.request_shutdown()
    await asyncio.wait_for(task, timeout=2.0)
    # Must not have waited the full 3600s interval for a periodic tick.
    assert ticks["n"] == 1


@pytest.mark.asyncio
async def test_injected_clock_flows_through_to_reconcile(session_factory):
    with session_factory() as s:
        _seed_published(s, tenant_id=TENANT_A)

    seen_now: list[datetime] = []
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        seen_now.append(now)
        return {"success": 0, "skipped": 0, "error": 0}

    driver = CorpusReconcilerDriver(
        authoring_session_factory=session_factory,
        reconcile_fn=_reconcile,
        now_fn=lambda: fixed,
    )
    driver.tick_once(reason="periodic")  # now omitted → now_fn
    assert seen_now == [fixed]

    # Explicit now on tick overrides now_fn.
    other = datetime(2026, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
    driver.tick_once(reason="periodic", now=other)
    assert seen_now[-1] == other


def test_list_published_tenant_ids_distinct(session_factory):
    with session_factory() as s:
        _seed_published(s, tenant_id=TENANT_A)
        _seed_published(s, tenant_id=TENANT_A, version="v2")
        assert list_published_tenant_ids(s) == [TENANT_A]
