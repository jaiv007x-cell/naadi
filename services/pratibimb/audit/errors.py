"""Audit trail errors — surfaced at the HTTP edge when audit is unavailable."""
from __future__ import annotations


class AuditWriteError(RuntimeError):
    """Audit sink refused a write — fail-closed catalog reads surface this at HTTP."""

    def __init__(
        self,
        message: str,
        *,
        correlation_id: str | None = None,
        retry_after_seconds: int | None = 30,
    ) -> None:
        super().__init__(message)
        self.correlation_id = correlation_id
        self.retry_after_seconds = retry_after_seconds
