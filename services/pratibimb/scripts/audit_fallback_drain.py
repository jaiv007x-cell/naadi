#!/usr/bin/env python3
"""
Replay audit-fallback JSONL into the primary audit DB.

Schedule: every 60s via cron when primary sink health check passes.
See docs/policy/audit_sink_failure_policy.md §4 and ledger_read/fallback_drain.py.
"""
from __future__ import annotations

import logging
import sys

from services.pratibimb.ledger_read.factory import (
    build_audit_session_factory,
    build_fallback_sink,
)
from services.pratibimb.ledger_read.fallback_drain import (
    FallbackDrainWorker,
    SessionPrimaryHealthCheck,
)
from services.pratibimb.audit.sink import SqlAuditSink

log = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    session_factory = build_audit_session_factory()
    fallback = build_fallback_sink()
    worker = FallbackDrainWorker(
        root_dir=fallback.root,
        primary=SqlAuditSink(session_factory),
        primary_health=SessionPrimaryHealthCheck(session_factory),
    )
    result = worker.drain_tick()
    if result.skipped_reason:
        log.warning("audit-fallback-drain skipped: %s", result.skipped_reason)
        return 0
    log.info(
        "audit-fallback-drain complete tenants=%s drained=%s skipped=%s",
        result.tenants_processed,
        result.records_drained,
        result.records_skipped,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
