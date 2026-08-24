"""
E2.3.b/c: corpus reconciler driver — startup + periodic sweeps.

Hook remains the latency fast path; this loop is the event-free safety net.
``tick_once`` = sequential sweep (periodic + tests).
``startup_sweep`` = bounded-concurrency startup (E2.3.c).
``run`` = startup_sweep once → wait/tick periodic.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Callable, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.pratibimb.authoring.db import authoring_session
from services.pratibimb.authoring.metrics import record_corpus_reconciler_pass
from services.pratibimb.authoring.models import PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_facade import ReconcileReason, reconcile_tenant
from shared.logging import get_logger

log = get_logger(__name__)

CORPUS_RECONCILE_INTERVAL_ENV = "CORPUS_RECONCILE_INTERVAL_SECONDS"
DEFAULT_RECONCILE_INTERVAL_SECONDS = 60.0
MIN_RECONCILE_INTERVAL_SECONDS = 15.0
MAX_RECONCILE_INTERVAL_SECONDS = 600.0

CORPUS_RECONCILE_STARTUP_CONCURRENCY_ENV = "CORPUS_RECONCILE_STARTUP_CONCURRENCY"
DEFAULT_STARTUP_CONCURRENCY = 4
MIN_STARTUP_CONCURRENCY = 1
MAX_STARTUP_CONCURRENCY = 16

AuthoringSessionFactory = Callable[[], AbstractContextManager[Session]]
ReconcileFn = Callable[..., dict[str, int]]
ListTenantsFn = Callable[[Session], list[str]]
NowFn = Callable[[], datetime]
PassResult = Literal["ok", "error"]


def resolve_reconcile_interval_seconds(
    raw: str | float | None = None,
) -> float:
    """
    Default 60s; clamp to [15, 600].

    ``raw`` overrides env when provided (tests). Env key:
    ``CORPUS_RECONCILE_INTERVAL_SECONDS``.
    """
    if raw is None:
        raw = os.getenv(CORPUS_RECONCILE_INTERVAL_ENV)
    if raw is None or raw == "":
        value = DEFAULT_RECONCILE_INTERVAL_SECONDS
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            log.warning(
                "invalid %s=%r; using default %s",
                CORPUS_RECONCILE_INTERVAL_ENV,
                raw,
                DEFAULT_RECONCILE_INTERVAL_SECONDS,
            )
            value = DEFAULT_RECONCILE_INTERVAL_SECONDS
    return max(
        MIN_RECONCILE_INTERVAL_SECONDS,
        min(MAX_RECONCILE_INTERVAL_SECONDS, value),
    )


def resolve_startup_concurrency(raw: str | int | None = None) -> int:
    """
    Default 4; clamp to [1, 16].

    ``1`` disables concurrency without a code change. Env:
    ``CORPUS_RECONCILE_STARTUP_CONCURRENCY``.
    """
    if raw is None:
        raw = os.getenv(CORPUS_RECONCILE_STARTUP_CONCURRENCY_ENV)
    if raw is None or raw == "":
        value = DEFAULT_STARTUP_CONCURRENCY
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            log.warning(
                "invalid %s=%r; using default %s",
                CORPUS_RECONCILE_STARTUP_CONCURRENCY_ENV,
                raw,
                DEFAULT_STARTUP_CONCURRENCY,
            )
            value = DEFAULT_STARTUP_CONCURRENCY
    return max(MIN_STARTUP_CONCURRENCY, min(MAX_STARTUP_CONCURRENCY, value))


def list_published_tenant_ids(authoring: Session) -> list[str]:
    """DISTINCT tenant_id with ≥1 published row — fresh each tick."""
    rows = authoring.scalars(
        select(PublishedCaseVersionRow.tenant_id).distinct()
    ).all()
    return sorted({str(t) for t in rows})


class CorpusReconcilerDriver:
    """
    Startup sweep (reason=startup, bounded concurrency) then periodic
    ticks (reason=periodic, sequential).

    Failure isolation: one tenant raising must not skip the rest.
    Pass-level metric records ok/error; row_total stays inside reconcile_tenant.
    No cross-tenant ordering guarantee under concurrent startup.
    """

    def __init__(
        self,
        *,
        interval_seconds: float | None = None,
        startup_concurrency: int | None = None,
        now_fn: NowFn | None = None,
        authoring_session_factory: AuthoringSessionFactory | None = None,
        reconcile_fn: ReconcileFn | None = None,
        list_tenants_fn: ListTenantsFn | None = None,
    ) -> None:
        self.interval_seconds = (
            resolve_reconcile_interval_seconds(interval_seconds)
            if interval_seconds is not None
            else resolve_reconcile_interval_seconds()
        )
        self.startup_concurrency = (
            resolve_startup_concurrency(startup_concurrency)
            if startup_concurrency is not None
            else resolve_startup_concurrency()
        )
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._authoring_session_factory = (
            authoring_session_factory or authoring_session
        )
        self._reconcile_fn = reconcile_fn or reconcile_tenant
        self._list_tenants_fn = list_tenants_fn or list_published_tenant_ids
        self._shutdown = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def _discover_tenants(self) -> list[str]:
        with self._authoring_session_factory() as discovery_session:
            return list(self._list_tenants_fn(discovery_session))

    def _reconcile_one(
        self,
        tenant_id: str,
        *,
        reason: ReconcileReason,
        now: datetime,
    ) -> PassResult:
        """Run one tenant pass; never raises — records pass ok/error."""
        try:
            with self._authoring_session_factory() as authoring:
                self._reconcile_fn(
                    tenant_id,
                    authoring_session=authoring,
                    reason=reason,
                    now=now,
                )
            record_corpus_reconciler_pass(
                tenant_id=tenant_id, reason=reason, outcome="ok"
            )
            return "ok"
        except Exception:
            log.exception(
                "corpus reconciler tenant pass failed "
                "(tenant_id=%s reason=%s); continuing remaining tenants",
                tenant_id,
                reason,
            )
            record_corpus_reconciler_pass(
                tenant_id=tenant_id, reason=reason, outcome="error"
            )
            return "error"

    def tick_once(
        self,
        *,
        reason: ReconcileReason,
        now: datetime | None = None,
    ) -> dict[str, PassResult]:
        """
        Sequential sweep (periodic path + unit tests).

        Returns ``{tenant_id: "ok"|"error"}``. Order of keys is discovery
        order; do not rely on order under concurrent startup.
        """
        clock = now if now is not None else self._now_fn()
        results: dict[str, PassResult] = {}
        for tenant_id in self._discover_tenants():
            results[tenant_id] = self._reconcile_one(
                tenant_id, reason=reason, now=clock
            )
        return results

    async def startup_sweep(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, PassResult]:
        """
        Bounded-concurrency startup pass (``reason=startup``).

        Up to ``startup_concurrency`` tenants reconcile at once. Periodic
        ticks stay on sequential ``tick_once``. No ordering guarantee across
        tenants — assert on sets of outcomes, not sequences.
        """
        clock = now if now is not None else self._now_fn()
        tenant_ids = await asyncio.to_thread(self._discover_tenants)
        if not tenant_ids:
            return {}

        sem = asyncio.Semaphore(self.startup_concurrency)
        results: dict[str, PassResult] = {}
        results_lock = asyncio.Lock()

        async def _one(tenant_id: str) -> None:
            async with sem:
                outcome = await asyncio.to_thread(
                    self._reconcile_one,
                    tenant_id,
                    reason="startup",
                    now=clock,
                )
                async with results_lock:
                    results[tenant_id] = outcome

        await asyncio.gather(
            *(_one(tid) for tid in tenant_ids),
            return_exceptions=True,
        )
        # _reconcile_one never raises; gather return_exceptions is belt for
        # unexpected failures in the wrapper (still must not cancel siblings).
        for tenant_id in tenant_ids:
            if tenant_id not in results:
                log.error(
                    "corpus reconciler startup worker vanished "
                    "(tenant_id=%s); recording pass error",
                    tenant_id,
                )
                record_corpus_reconciler_pass(
                    tenant_id=tenant_id, reason="startup", outcome="error"
                )
                results[tenant_id] = "error"
        return results

    async def run(self) -> None:
        """Concurrent startup sweep, then sequential periodic ticks."""
        await self.startup_sweep()
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self.interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                await asyncio.to_thread(self.tick_once, reason="periodic")

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._shutdown.clear()
        self._task = asyncio.create_task(self.run(), name="corpus-reconciler")

    async def stop(self) -> None:
        self.request_shutdown()
        if self._task is not None:
            try:
                await self._task
            finally:
                self._task = None
