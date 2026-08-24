"""SAMVAAD.b — samvaad_verifier audit emit (shared ledger_read_audit).

Uses caller_kind=samvaad_verifier. SAMVAAD.c opens the one production Call site
on POST /v1/samvaad/verify — always invoked; flag gates live_emit stamp only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.caller_kinds import assert_known_caller_kind

CALLER_KIND = "samvaad_verifier"

# In-memory emit log for tests — production may also write via AuditSink
_EMIT_LOG: list[dict[str, Any]] = []


class AuditSinkLike(Protocol):
    def emit(self, event: Any) -> None: ...


@dataclass(frozen=True)
class SamvaadVerifierEmit:
    query_id: str
    caller_kind: str
    outcome: str
    at_utc: datetime
    params: dict[str, Any]


def clear_verifier_emits() -> None:
    _EMIT_LOG.clear()


def verifier_emits() -> list[dict[str, Any]]:
    return list(_EMIT_LOG)


def emit_samvaad_verifier_audit(
    *,
    outcome: str = "ok",
    params: dict[str, Any] | None = None,
    sink: AuditSinkLike | None = None,
    fail_closed: bool = False,
) -> SamvaadVerifierEmit:
    """Always-invoked emit surface — one production Call site (SAMVAAD.c)."""
    assert_known_caller_kind(CALLER_KIND)
    if CALLER_KIND != "samvaad_verifier":
        raise RuntimeError("caller_kind drift")
    merged = dict(params or {})
    event = SamvaadVerifierEmit(
        query_id=str(uuid4()),
        caller_kind=CALLER_KIND,
        outcome=outcome,
        at_utc=datetime.now(timezone.utc),
        params=merged,
    )
    row = {
        "query_id": event.query_id,
        "caller_kind": event.caller_kind,
        "outcome": event.outcome,
        "at_utc": event.at_utc.isoformat(),
        "params": event.params,
    }
    _EMIT_LOG.append(row)
    if sink is not None:
        try:
            sink.emit(row)
        except Exception as exc:
            if fail_closed:
                raise AuditWriteError(
                    "samvaad_verifier_audit_unavailable",
                    correlation_id=event.query_id,
                ) from exc
            raise
    return event


# .b stub keys — .c emit params must be a superset (matrix #14)
B_STUB_EMIT_PARAM_KEYS: frozenset[str] = frozenset({"path"})

