"""G.b: revoke + signed status-list publish / fetch (I-G-5)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import AUDIT_SINK_FAILURE_TOTAL
from services.pratibimb.audit.sink import AuditEvent, AuditSink, hash_params
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.credentials.sign import DEFAULT_ISSUER_KEY_ID
from services.pratibimb.credentials.status_list import (
    DEFAULT_LIST_TTL,
    STATUS_LIST_SCHEMA_V1,
    build_status_list_envelope,
    new_snapshot_id,
)
from services.pratibimb.ledger.models import (
    CredentialLedgerRow,
    CredentialStatusListSnapshotRow,
    StatusListIdentifierRow,
)
from services.pratibimb.ledger_read.errors import ScopeDeniedError, TenantBoundaryError
from shared.schemas.ledger_read import ConsentScope

_VERIFIER_CALLER = "ncvet_verifier"
_SURFACE = "credentials"
STATUS_LIST_QUERY_KIND = "fetch_credential_status_list"
STATUS_LIST_PAGE_CAP = 20


class CredentialNotFoundError(LookupError):
    """Unknown credential_id for tenant."""


class CredentialAlreadyRevokedError(LookupError):
    """revoked_at already set."""


class CredentialStatusService:
    """Revoke credentials and serve append-only signed status-list snapshots."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        audit_sink: AuditSink,
        *,
        now: Callable[[], datetime] | None = None,
        issuer_key_id: str = DEFAULT_ISSUER_KEY_ID,
        list_ttl=DEFAULT_LIST_TTL,
    ) -> None:
        self._session_factory = session_factory
        self._sink = audit_sink
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._issuer_key_id = issuer_key_id
        self._list_ttl = list_ttl

    def _publish_snapshot(self, session: Session, *, tenant_id: str) -> dict[str, Any]:
        revoked = list(
            session.execute(
                select(CredentialLedgerRow)
                .where(CredentialLedgerRow.tenant_id == tenant_id)
                .where(CredentialLedgerRow.revoked_at.is_not(None))
                .order_by(CredentialLedgerRow.revoked_at.asc())
            ).scalars()
        )
        # Latest version per credential_id only (revocation is per credential_id).
        by_id: dict[str, CredentialLedgerRow] = {}
        for row in revoked:
            prev = by_id.get(row.credential_id)
            if prev is None or row.credential_version >= prev.credential_version:
                by_id[row.credential_id] = row
        entries = [
            {
                "credential_id": r.credential_id,
                "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
            }
            for r in by_id.values()
        ]
        signed_at = self._now()
        valid_until = signed_at + self._list_ttl
        envelope = build_status_list_envelope(
            entries=entries,
            key_id=self._issuer_key_id,
            signed_at=signed_at,
            valid_until=valid_until,
        )
        for r in by_id.values():
            existing = session.get(
                StatusListIdentifierRow,
                (tenant_id, "credential", r.credential_id),
            )
            if existing is None:
                session.add(
                    StatusListIdentifierRow(
                        tenant_id=tenant_id,
                        identifier_kind="credential",
                        identifier_id=r.credential_id,
                        revoked_at=r.revoked_at,
                        created_at=signed_at,
                    )
                )
        snap = CredentialStatusListSnapshotRow(
            snapshot_id=new_snapshot_id(),
            tenant_id=tenant_id,
            status_list_schema_version=STATUS_LIST_SCHEMA_V1,
            key_id=self._issuer_key_id,
            signed_at=signed_at,
            valid_until=valid_until,
            envelope_bytes=json.dumps(envelope, sort_keys=True, default=str),
            created_at=signed_at,
        )
        session.add(snap)
        return envelope

    async def revoke(
        self,
        *,
        auth: AuthContext,
        credential_id: str,
        scope: ConsentScope,
    ) -> dict[str, Any]:
        if scope is not ConsentScope.NCVET_REVOKE_CREDENTIAL:
            raise ScopeDeniedError(
                required=frozenset({ConsentScope.NCVET_REVOKE_CREDENTIAL}),
                granted=frozenset({scope}),
            )
        with self._session_factory() as session:
            rows = list(
                session.execute(
                    select(CredentialLedgerRow)
                    .where(CredentialLedgerRow.credential_id == credential_id)
                    .order_by(CredentialLedgerRow.credential_version.desc())
                ).scalars()
            )
            if not rows:
                raise CredentialNotFoundError(credential_id)
            latest = rows[0]
            if latest.tenant_id != auth.tenant_id:
                raise TenantBoundaryError(
                    requested=latest.tenant_id,
                    auth_tenant=auth.tenant_id,
                )
            if latest.revoked_at is not None:
                raise CredentialAlreadyRevokedError(credential_id)
            now = self._now()
            # Revoke all versions of this credential_id; leave evidence_ref alone.
            for row in rows:
                if row.revoked_at is None:
                    row.revoked_at = now
            envelope = self._publish_snapshot(session, tenant_id=auth.tenant_id)
            session.commit()
        return {
            "credential_id": credential_id,
            "revoked_at": now.isoformat(),
            "status_list": envelope,
        }

    async def fetch_status_list(
        self,
        *,
        auth: AuthContext,
        scope: ConsentScope,
        cursor: str | None = None,
        limit: int = STATUS_LIST_PAGE_CAP,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if scope is not ConsentScope.NCVET_FETCH_STATUS_LIST:
            raise ScopeDeniedError(
                required=frozenset({ConsentScope.NCVET_FETCH_STATUS_LIST}),
                granted=frozenset({scope}),
            )
        page_limit = min(max(1, limit), STATUS_LIST_PAGE_CAP)
        params = {
            "tenant_id": auth.tenant_id,
            "cursor": cursor,
            "limit": page_limit,
        }
        started = self._now()
        with self._session_factory() as session:
            q = (
                select(CredentialStatusListSnapshotRow)
                .where(CredentialStatusListSnapshotRow.tenant_id == auth.tenant_id)
                .order_by(CredentialStatusListSnapshotRow.signed_at.desc())
            )
            rows = list(session.execute(q).scalars())
            if cursor:
                # cursor = snapshot_id; return pages after that id in signed_at desc order
                ids = [r.snapshot_id for r in rows]
                if cursor in ids:
                    rows = rows[ids.index(cursor) + 1 :]
            page = rows[:page_limit]
            items = [json.loads(r.envelope_bytes) for r in page]
            next_cursor = page[-1].snapshot_id if len(rows) > page_limit else None

        duration_ms = int((self._now() - started).total_seconds() * 1000)
        ph, pb = hash_params(params)
        event = AuditEvent(
            query_id=str(uuid.uuid4()),
            at_utc=started,
            tenant_id=auth.tenant_id,
            subject_pseudo_id=auth.subject_pseudo_id,
            actor_subject_id=auth.subject_pseudo_id,
            caller_kind=_VERIFIER_CALLER,
            scope=scope.value,
            query_kind=STATUS_LIST_QUERY_KIND,
            query_params_hash=ph,
            query_params_bytes=pb,
            outcome="ok",
            duration_ms=duration_ms,
            result_row_count=len(items),
            request_id=request_id,
        )
        try:
            self._sink.emit(event)
        except Exception as exc:
            AUDIT_SINK_FAILURE_TOTAL.labels(
                sink="primary",
                surface=_SURFACE,
                tenant_id=auth.tenant_id,
            ).inc()
            raise AuditWriteError(
                "credentials_status_list_audit_unavailable",
                correlation_id=event.query_id,
                retry_after_seconds=30,
            ) from exc
        return {
            "items": items,
            "next_cursor": next_cursor,
            "page_cap": STATUS_LIST_PAGE_CAP,
        }
