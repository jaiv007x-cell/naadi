"""E2.3.c: startup sweep bounded concurrency (N=4 default, clamp 1–16)."""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager

import pytest

from services.pratibimb.authoring.metrics import (
    CORPUS_RECONCILER_PASS_TOTAL,
    reset_authoring_metrics_for_tests,
)
from services.pratibimb.ledger.corpus_reconciler import (
    CorpusReconcilerDriver,
    resolve_startup_concurrency,
)

TENANTS = [f"tenant-{i}" for i in range(8)]


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_authoring_metrics_for_tests()
    yield
    reset_authoring_metrics_for_tests()


@contextmanager
def _dummy_session():
    yield object()


def _metric_value(counter, **labels) -> float:
    key = tuple(sorted(labels.items()))
    with counter._lock:
        return float(counter._values.get(key, 0.0))


def test_resolve_startup_concurrency_default_and_clamp():
    assert resolve_startup_concurrency(None) == 4
    assert resolve_startup_concurrency(0) == 1
    assert resolve_startup_concurrency(99) == 16
    assert resolve_startup_concurrency(1) == 1
    assert resolve_startup_concurrency(8) == 8


def test_injected_n_flows_through_to_semaphore():
    driver = CorpusReconcilerDriver(startup_concurrency=7)
    assert driver.startup_concurrency == 7
    assert (
        CorpusReconcilerDriver(startup_concurrency=100).startup_concurrency == 16
    )


@pytest.mark.asyncio
async def test_startup_n4_runs_up_to_four_tenants_concurrently():
    """Controllable reconcile_fn blocks; peak in-flight must hit N=4."""
    lock = threading.Lock()
    active = 0
    peak = 0
    released = threading.Event()
    hit_n = threading.Event()

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if peak >= 4:
                hit_n.set()
        assert released.wait(timeout=5.0), "release never signaled"
        with lock:
            active -= 1
        return {"success": 0, "skipped": 0, "error": 0}

    driver = CorpusReconcilerDriver(
        startup_concurrency=4,
        authoring_session_factory=_dummy_session,
        reconcile_fn=_reconcile,
        list_tenants_fn=lambda _s: list(TENANTS),
    )
    task = asyncio.create_task(driver.startup_sweep())
    for _ in range(300):
        if hit_n.is_set():
            break
        await asyncio.sleep(0.01)
    assert hit_n.is_set(), f"never reached 4 concurrent (peak={peak})"
    assert peak == 4
    released.set()
    results = await asyncio.wait_for(task, timeout=5.0)
    assert set(results) == set(TENANTS)
    assert all(v == "ok" for v in results.values())


@pytest.mark.asyncio
async def test_startup_n1_serializes():
    lock = threading.Lock()
    active = 0
    peak = 0
    released = threading.Event()
    entered_first = threading.Event()

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            entered_first.set()
        time.sleep(0.05)
        assert released.wait(timeout=5.0)
        with lock:
            active -= 1
        return {"success": 0, "skipped": 0, "error": 0}

    driver = CorpusReconcilerDriver(
        startup_concurrency=1,
        authoring_session_factory=_dummy_session,
        reconcile_fn=_reconcile,
        list_tenants_fn=lambda _s: list(TENANTS[:4]),
    )
    task = asyncio.create_task(driver.startup_sweep())
    for _ in range(200):
        if entered_first.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered_first.is_set()
    await asyncio.sleep(0.15)
    assert peak == 1
    released.set()
    results = await asyncio.wait_for(task, timeout=5.0)
    assert set(results) == set(TENANTS[:4])
    assert peak == 1


@pytest.mark.asyncio
async def test_one_tenant_failing_does_not_cancel_siblings_under_concurrency():
    fail_id = "tenant-boom"
    ok_ids = ["tenant-ok-1", "tenant-ok-2", "tenant-ok-3"]

    def _reconcile(tenant_id, *, authoring_session, reason, now, **_kw):
        if tenant_id == fail_id:
            raise RuntimeError("boom")
        return {"success": 0, "skipped": 0, "error": 0}

    driver = CorpusReconcilerDriver(
        startup_concurrency=4,
        authoring_session_factory=_dummy_session,
        reconcile_fn=_reconcile,
        list_tenants_fn=lambda _s: [fail_id, *ok_ids],
    )
    results = await driver.startup_sweep()
    assert results[fail_id] == "error"
    assert {tid for tid, o in results.items() if o == "ok"} == set(ok_ids)
    assert (
        _metric_value(
            CORPUS_RECONCILER_PASS_TOTAL,
            tenant=fail_id,
            reason="startup",
            outcome="error",
        )
        == 1.0
    )
    for tid in ok_ids:
        assert (
            _metric_value(
                CORPUS_RECONCILER_PASS_TOTAL,
                tenant=tid,
                reason="startup",
                outcome="ok",
            )
            == 1.0
        )
