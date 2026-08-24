"""Startup/periodic exact-convergence driver for SAMVAAD Dhaara projection."""
from __future__ import annotations

import asyncio
import os
from contextlib import AbstractContextManager
from typing import Callable

from sqlalchemy.orm import Session

from services.pratibimb.ledger.db import ledger_session
from services.pratibimb.samvaad.dhaara_projection import (
    ProjectionSink,
    reconcile_projection,
)

RECONCILE_INTERVAL_ENV = "SAMVAAD_DHAARA_RECONCILE_INTERVAL_SECONDS"
DEFAULT_RECONCILE_INTERVAL_SECONDS = 60.0
MIN_RECONCILE_INTERVAL_SECONDS = 15.0
MAX_RECONCILE_INTERVAL_SECONDS = 600.0

SessionFactory = Callable[[], AbstractContextManager[Session]]


def resolve_reconcile_interval_seconds(raw: str | float | None = None) -> float:
    if raw is None:
        raw = os.getenv(RECONCILE_INTERVAL_ENV)
    try:
        value = (
            DEFAULT_RECONCILE_INTERVAL_SECONDS
            if raw is None or raw == ""
            else float(raw)
        )
    except (TypeError, ValueError):
        value = DEFAULT_RECONCILE_INTERVAL_SECONDS
    return max(
        MIN_RECONCILE_INTERVAL_SECONDS,
        min(MAX_RECONCILE_INTERVAL_SECONDS, value),
    )


class SamvaadProjectionReconciler:
    def __init__(
        self,
        sink: ProjectionSink,
        *,
        session_factory: SessionFactory = ledger_session,
        interval_seconds: float | None = None,
    ) -> None:
        self.sink = sink
        self.session_factory = session_factory
        self.interval_seconds = resolve_reconcile_interval_seconds(interval_seconds)
        self._shutdown = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def tick_once(self) -> dict[str, int]:
        with self.session_factory() as session:
            return reconcile_projection(session, self.sink)

    async def run(self) -> None:
        await asyncio.to_thread(self.tick_once)
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self.interval_seconds,
                )
            except asyncio.TimeoutError:
                await asyncio.to_thread(self.tick_once)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._shutdown.clear()
        self._task = asyncio.create_task(self.run(), name="samvaad-reconciler")

    async def stop(self) -> None:
        self._shutdown.set()
        if self._task is not None:
            await self._task
            self._task = None
