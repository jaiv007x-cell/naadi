"""Append-only local JSONL fallback when the primary audit sink fails."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from services.pratibimb.audit.metrics import (
    AUDIT_SINK_EMIT_TOTAL,
    AUDIT_SINK_FAILURE_TOTAL,
)
from services.pratibimb.audit.sink import AuditEvent

log = logging.getLogger(__name__)


class JsonlFallbackSink:
    """
    Append-only local JSONL sink. Used only when the primary sink fails.

    Contract:
      - fsync per line (durability over throughput; audit volume is low).
      - One file per tenant per UTC day.
      - Never raises to caller — this IS the last line of defense.
        If this sink fails, the service transitions to critical mode
        via the health check, not via a raised exception here.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_failure_at: datetime | None = None

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, tenant_id: str, at_utc: datetime) -> Path:
        day = at_utc.strftime("%Y-%m-%d")
        tenant_dir = self._root / tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir / f"{day}.jsonl"

    def emit(self, evt: AuditEvent, *, fallback_reason: str | None = None) -> bool:
        """Write event to JSONL. Returns True on success, False on failure."""
        try:
            path = self._path_for(evt.tenant_id, evt.at_utc)
            payload = evt.canonical_dict()
            if fallback_reason is not None:
                payload["fallback_reason"] = fallback_reason
            line = (
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
            with self._lock:
                fd = os.open(
                    str(path),
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o640,
                )
                try:
                    os.write(fd, line.encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
            AUDIT_SINK_EMIT_TOTAL.labels(
                sink="fallback", outcome="ok", tenant_id=evt.tenant_id
            ).inc()
            return True
        except Exception as exc:
            self._last_failure_at = datetime.now(timezone.utc)
            AUDIT_SINK_FAILURE_TOTAL.labels(
                sink="fallback", tenant_id=evt.tenant_id
            ).inc()
            log.critical(
                "audit_fallback_sink_failed query_id=%s err=%r",
                evt.query_id,
                exc,
            )
            return False

    def is_healthy(self, within_seconds: int = 60) -> bool:
        if self._last_failure_at is None:
            return True
        age = (datetime.now(timezone.utc) - self._last_failure_at).total_seconds()
        return age > within_seconds

    def read_lines(self, tenant_id: str, day: str) -> list[dict]:
        """Test/helper — read parsed JSONL lines for a tenant/day."""
        path = self._root / tenant_id / f"{day}.jsonl"
        if not path.exists():
            return []
        lines: list[dict] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                lines.append(json.loads(raw))
        return lines
