"""AuditSink protocol + implementations."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Protocol

from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import IntegrityError

from services.pratibimb.audit.caller_kinds import assert_known_caller_kind
from services.pratibimb.audit.models_audit import LedgerReadAuditRow
from services.pratibimb.ledger_read.query_kinds import (
    E3_AUTHORING_CATALOG_KINDS,
    F_NCVET_QUERY_KINDS,
)

log = logging.getLogger(__name__)


def validate_audit_event(event: "AuditEvent") -> None:
    """E3/F: catalog + NCVET kinds require actor_subject_id."""
    assert_known_caller_kind(event.caller_kind)
    if event.query_kind in E3_AUTHORING_CATALOG_KINDS and not event.actor_subject_id:
        raise ValueError(
            f"actor_subject_id required for catalog query_kind={event.query_kind!r}"
        )
    if event.query_kind in F_NCVET_QUERY_KINDS and not event.actor_subject_id:
        raise ValueError(
            f"actor_subject_id required for ncvet query_kind={event.query_kind!r}"
        )


@dataclass(frozen=True)
class AuditEvent:
    query_id: str
    at_utc: datetime
    tenant_id: str
    subject_pseudo_id: str
    caller_kind: str
    scope: str
    query_kind: str
    query_params_hash: str
    query_params_bytes: int
    outcome: str
    duration_ms: int
    actor_subject_id: Optional[str] = None
    result_row_count: Optional[int] = None
    k_anonymity_floor_applied: Optional[int] = None
    error_kind: Optional[str] = None
    request_id: Optional[str] = None
    result_fingerprint: Optional[str] = None

    def canonical_dict(self) -> dict[str, Any]:
        """JSON-serializable canonical form for fallback JSONL drain."""
        data = asdict(self)
        data["at_utc"] = self.at_utc.isoformat()
        return data


class AuditSink(Protocol):
    def emit(self, event: AuditEvent) -> None: ...


class SqlAuditSink:
    """Writes to ledger_read_audit in an independent transaction."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def emit(self, event: AuditEvent) -> None:
        validate_audit_event(event)
        session = self._session_factory()
        try:
            session.add(
                LedgerReadAuditRow(
                    query_id=event.query_id,
                    at_utc=event.at_utc,
                    tenant_id=event.tenant_id,
                    subject_pseudo_id=event.subject_pseudo_id,
                    actor_subject_id=event.actor_subject_id,
                    caller_kind=event.caller_kind,
                    scope=event.scope,
                    query_kind=event.query_kind,
                    query_params_hash=event.query_params_hash,
                    query_params_bytes=event.query_params_bytes,
                    outcome=event.outcome,
                    result_row_count=event.result_row_count,
                    k_anonymity_floor_applied=event.k_anonymity_floor_applied,
                    error_kind=event.error_kind,
                    request_id=event.request_id,
                    duration_ms=event.duration_ms,
                    result_fingerprint=event.result_fingerprint,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            log.exception(
                "ledger_read_audit.emit_failed query_id=%s tenant=%s kind=%s outcome=%s",
                event.query_id,
                event.tenant_id,
                event.query_kind,
                event.outcome,
            )
            raise
        finally:
            session.close()

    def emit_idempotent(self, event: AuditEvent) -> bool:
        """Insert audit row; return True if persisted or already present."""
        validate_audit_event(event)
        session = self._session_factory()
        try:
            if session.get(LedgerReadAuditRow, event.query_id) is not None:
                return True
            session.add(
                LedgerReadAuditRow(
                    query_id=event.query_id,
                    at_utc=event.at_utc,
                    tenant_id=event.tenant_id,
                    subject_pseudo_id=event.subject_pseudo_id,
                    actor_subject_id=event.actor_subject_id,
                    caller_kind=event.caller_kind,
                    scope=event.scope,
                    query_kind=event.query_kind,
                    query_params_hash=event.query_params_hash,
                    query_params_bytes=event.query_params_bytes,
                    outcome=event.outcome,
                    result_row_count=event.result_row_count,
                    k_anonymity_floor_applied=event.k_anonymity_floor_applied,
                    error_kind=event.error_kind,
                    request_id=event.request_id,
                    duration_ms=event.duration_ms,
                    result_fingerprint=event.result_fingerprint,
                )
            )
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@dataclass
class InMemoryAuditSink:
    """Test double. Thread-unsafe by design — tests are single-threaded."""

    events: list[AuditEvent] = field(default_factory=list)

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def by_kind(self, kind: str) -> list[AuditEvent]:
        return [e for e in self.events if e.query_kind == kind]

    def last(self) -> AuditEvent:
        return self.events[-1]


def hash_params(params: Any) -> tuple[str, int]:
    """Deterministic canonical hash + byte length of a query filter."""
    if hasattr(params, "model_dump"):
        payload = params.model_dump(mode="json")
    elif hasattr(params, "__dict__") and not isinstance(params, type):
        payload = params.__dict__
    else:
        payload = params
    payload = _scrub_payload(payload)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _scrub_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _scrub_payload(v)
            for k, v in value.items()
            if v is not None and v != ""
        }
    if isinstance(value, list):
        return [_scrub_payload(v) for v in value]
    return value


def make_sql_audit_sink(engine) -> SqlAuditSink:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return SqlAuditSink(factory)
