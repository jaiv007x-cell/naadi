"""Crash-safe replay of fallback JSONL into the primary audit sink."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from services.pratibimb.audit.metrics import AUDIT_FALLBACK_DRAIN_LAG_SECONDS
from services.pratibimb.audit.sink import AuditEvent

log = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1000


class PrimarySink(Protocol):
    def emit_idempotent(self, event: AuditEvent) -> bool: ...


class PrimaryHealthCheck(Protocol):
    def is_healthy(self) -> bool: ...


@dataclass(frozen=True)
class DrainTickResult:
    skipped_reason: str | None = None
    tenants_processed: int = 0
    records_drained: int = 0
    records_skipped: int = 0


@dataclass
class FallbackDrainWorker:
    """
    Replays undrained fallback JSONL lines into the primary sink.

    Progress is tracked in a sidecar ``{day}.jsonl.drained`` file (one query_id
    per line, fsync'd). Source JSONL lines are never deleted in place.
    """

    root_dir: Path
    primary: PrimarySink
    primary_health: PrimaryHealthCheck
    batch_size_per_tenant: int = DEFAULT_BATCH_SIZE
    clock: Callable[[], datetime] | None = None
    _tenant_cursor: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root_dir = Path(self.root_dir)
        self.clock = self.clock or (lambda: datetime.now(timezone.utc))

    def drained_marker_for(self, jsonl_path: Path) -> Path:
        return jsonl_path.with_name(jsonl_path.name + ".drained")

    def list_pending_files(self) -> list[tuple[str, Path]]:
        pending: list[tuple[str, Path]] = []
        if not self.root_dir.exists():
            return pending
        for tenant_dir in sorted(self.root_dir.iterdir()):
            if not tenant_dir.is_dir():
                continue
            tenant_id = tenant_dir.name
            for jsonl_path in sorted(tenant_dir.glob("*.jsonl")):
                if jsonl_path.name.endswith(".jsonl.drained"):
                    continue
                if self._has_undrained_lines(jsonl_path):
                    pending.append((tenant_id, jsonl_path))
        return pending

    def _has_undrained_lines(self, jsonl_path: Path) -> bool:
        drained = self._load_drained_ids(self.drained_marker_for(jsonl_path))
        if not jsonl_path.exists():
            return False
        for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            if row.get("query_id") not in drained:
                return True
        return False

    def _load_drained_ids(self, marker: Path) -> set[str]:
        if not marker.exists():
            return set()
        ids: set[str] = set()
        for raw in marker.read_text(encoding="utf-8").splitlines():
            qid = raw.strip()
            if qid:
                ids.add(qid)
        return ids

    def _append_drained_marker(self, marker: Path, query_id: str) -> None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        line = query_id + "\n"
        fd = os.open(
            str(marker),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o640,
        )
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def event_from_row(self, row: dict) -> AuditEvent:
        at_raw = row["at_utc"]
        at_utc = (
            datetime.fromisoformat(at_raw)
            if isinstance(at_raw, str)
            else at_raw
        )
        if at_utc.tzinfo is None:
            at_utc = at_utc.replace(tzinfo=timezone.utc)
        return AuditEvent(
            query_id=row["query_id"],
            at_utc=at_utc,
            tenant_id=row["tenant_id"],
            subject_pseudo_id=row["subject_pseudo_id"],
            caller_kind=row["caller_kind"],
            scope=row["scope"],
            query_kind=row["query_kind"],
            query_params_hash=row["query_params_hash"],
            query_params_bytes=int(row["query_params_bytes"]),
            outcome=row["outcome"],
            duration_ms=int(row["duration_ms"]),
            result_row_count=row.get("result_row_count"),
            k_anonymity_floor_applied=row.get("k_anonymity_floor_applied"),
            error_kind=row.get("error_kind"),
            request_id=row.get("request_id"),
            result_fingerprint=row.get("result_fingerprint"),
        )

    def oldest_undrained_at(self, tenant_id: str) -> datetime | None:
        tenant_dir = self.root_dir / tenant_id
        if not tenant_dir.is_dir():
            return None
        oldest: datetime | None = None
        for jsonl_path in sorted(tenant_dir.glob("*.jsonl")):
            if jsonl_path.name.endswith(".jsonl.drained"):
                continue
            drained = self._load_drained_ids(self.drained_marker_for(jsonl_path))
            for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                row = json.loads(raw)
                qid = row.get("query_id")
                if qid in drained:
                    continue
                evt = self.event_from_row(row)
                if oldest is None or evt.at_utc < oldest:
                    oldest = evt.at_utc
        return oldest

    def update_lag_metrics(self, tenant_ids: list[str] | None = None) -> None:
        now = self.clock()
        if tenant_ids is None:
            tenant_ids = sorted(
                {
                    tenant
                    for tenant, _ in self.list_pending_files()
                }
            )
            # Also zero lag for tenants with empty fallback dirs we scanned
            if self.root_dir.exists():
                tenant_ids = sorted(
                    set(tenant_ids)
                    | {
                        p.name
                        for p in self.root_dir.iterdir()
                        if p.is_dir()
                    }
                )
        for tenant_id in tenant_ids:
            oldest = self.oldest_undrained_at(tenant_id)
            lag = 0.0 if oldest is None else max(0.0, (now - oldest).total_seconds())
            AUDIT_FALLBACK_DRAIN_LAG_SECONDS.labels(tenant_id=tenant_id).set(lag)

    def drain_file(
        self,
        jsonl_path: Path,
        *,
        limit: int,
        after_write: Callable[[str], None] | None = None,
    ) -> tuple[int, int]:
        """
        Drain up to ``limit`` undrained lines from one JSONL file.

        ``after_write`` is invoked after a successful primary write and before
        the drained marker is appended — for crash-safety tests.
        """
        marker = self.drained_marker_for(jsonl_path)
        drained_ids = self._load_drained_ids(marker)
        drained_count = 0
        skipped = 0

        if not jsonl_path.exists():
            return 0, 0

        for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
            if drained_count >= limit:
                break
            if not raw.strip():
                continue
            row = json.loads(raw)
            query_id = row["query_id"]
            if query_id in drained_ids:
                skipped += 1
                continue

            event = self.event_from_row(row)
            self.primary.emit_idempotent(event)
            if after_write is not None:
                after_write(query_id)
            self._append_drained_marker(marker, query_id)
            drained_ids.add(query_id)
            drained_count += 1

        return drained_count, skipped

    def drain_tick(self) -> DrainTickResult:
        if not self.primary_health.is_healthy():
            log.warning("fallback_drain skipped: primary sink unhealthy")
            self.update_lag_metrics()
            return DrainTickResult(skipped_reason="primary_sink_unhealthy")

        pending = self.list_pending_files()
        if not pending:
            self.update_lag_metrics()
            return DrainTickResult()

        # Round-robin starting at cursor.
        ordered = pending[self._tenant_cursor :] + pending[: self._tenant_cursor]
        tenants_processed = 0
        records_drained = 0
        records_skipped = 0
        touched_tenants: set[str] = set()

        for tenant_id, jsonl_path in ordered:
            drained, skipped = self.drain_file(
                jsonl_path, limit=self.batch_size_per_tenant
            )
            if drained or skipped:
                tenants_processed += 1
                touched_tenants.add(tenant_id)
            records_drained += drained
            records_skipped += skipped
            if drained >= self.batch_size_per_tenant:
                # Advance cursor to next tenant after a full batch.
                idx = pending.index((tenant_id, jsonl_path))
                self._tenant_cursor = (idx + 1) % len(pending)
                break
        else:
            if pending:
                self._tenant_cursor = (self._tenant_cursor + 1) % len(pending)

        self.update_lag_metrics(sorted(touched_tenants) if touched_tenants else None)
        log.info(
            "fallback_drain tick tenants=%s drained=%s skipped=%s",
            tenants_processed,
            records_drained,
            records_skipped,
        )
        return DrainTickResult(
            tenants_processed=tenants_processed,
            records_drained=records_drained,
            records_skipped=records_skipped,
        )


class SessionPrimaryHealthCheck:
    """True when the audit DB accepts a trivial read."""

    def __init__(self, session_factory: Callable) -> None:
        self._session_factory = session_factory

    def is_healthy(self) -> bool:
        session = self._session_factory()
        try:
            session.connection().exec_driver_sql("SELECT 1")
            return True
        except Exception as exc:
            log.warning("primary_sink_health_check failed: %r", exc)
            return False
        finally:
            session.close()
