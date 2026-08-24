"""
Durable and in-memory ConsentGrantStore implementations.

Append-only grant/revoke history; effective scope is projected at query time.
Point-in-time resolution supports historical evidence queries.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.pratibimb.ledger.models import ConsentGrantRow
from shared.schemas.consent import (
    ConsentGrantEventType,
    ConsentGrantRecord,
    ConsentGrantSource,
    ConsentScopeDescriptor,
)
from shared.schemas.ledger_read import ConsentScope


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _scope_dict(scope: ConsentScope, resource_id: str) -> dict:
    return ConsentScopeDescriptor(
        consent_scope=scope, resource_id=resource_id
    ).model_dump(mode="json")


def _row_to_record(row: ConsentGrantRow) -> ConsentGrantRecord:
    desc = ConsentScopeDescriptor.model_validate(row.scope)
    return ConsentGrantRecord(
        grant_id=row.grant_id,
        subject_id=row.subject_id,
        tenant_id=row.tenant_id,
        scope=desc,
        event_type=ConsentGrantEventType(row.event_type),
        granted_at=_ensure_utc(row.granted_at),
        granted_by=row.granted_by,
        revoked_at=_ensure_utc(row.revoked_at) if row.revoked_at is not None else None,
        revoked_by=row.revoked_by,
        revoke_reason_code=row.revoke_reason_code,
        supersedes_grant_id=row.supersedes_grant_id,
        source=ConsentGrantSource(row.source) if row.source else None,
    )


def _project_active_grants(
    events: list[ConsentGrantRecord], at: datetime
) -> list[ConsentGrantRecord]:
    """Project grant events active at `at` from append-only history."""
    grants_by_id: dict[str, ConsentGrantRecord] = {}
    inactive: set[str] = set()

    for event in sorted(events, key=lambda e: e.effective_at):
        if event.effective_at > at:
            continue
        if event.event_type == ConsentGrantEventType.REVOKE:
            if event.supersedes_grant_id:
                inactive.add(event.supersedes_grant_id)
            continue
        grants_by_id[event.grant_id] = event
        if event.supersedes_grant_id:
            inactive.add(event.supersedes_grant_id)

    return [g for gid, g in grants_by_id.items() if gid not in inactive]


def _matches_grant(
    record: ConsentGrantRecord,
    *,
    scope: ConsentScope,
    resource_id: str,
) -> bool:
    if record.scope.consent_scope != scope:
        return False
    if record.scope.resource_id not in (resource_id, "*"):
        return False
    return True


class ConsentGrantStore(Protocol):
    async def has_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        scope: ConsentScope,
        resource_id: str,
        at: datetime | None = None,
    ) -> bool: ...

    async def resolve_scope(
        self,
        subject_id: str,
        tenant_id: str,
        at: datetime,
    ) -> frozenset[ConsentScope]: ...

    async def issue_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        scope: ConsentScope,
        resource_id: str,
        granted_by: str,
        source: ConsentGrantSource,
        granted_at: datetime | None = None,
        supersedes_grant_id: str | None = None,
    ) -> ConsentGrantRecord: ...

    async def revoke_grant(
        self,
        grant_id: str,
        *,
        revoked_by: str,
        revoke_reason_code: str,
        revoked_at: datetime | None = None,
    ) -> ConsentGrantRecord | None: ...


class InMemoryConsentGrantStore:
    """Test double — must stay behavior-identical to PostgresConsentGrantStore."""

    def __init__(self, events: list[ConsentGrantRecord] | None = None) -> None:
        self._events: list[ConsentGrantRecord] = list(events or [])

    def _events_for(
        self, *, tenant_id: str, subject_id: str
    ) -> list[ConsentGrantRecord]:
        return [
            e
            for e in self._events
            if e.tenant_id == tenant_id and e.subject_id == subject_id
        ]

    async def has_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        scope: ConsentScope,
        resource_id: str,
        at: datetime | None = None,
    ) -> bool:
        at = at or _utcnow()
        active = _project_active_grants(
            self._events_for(tenant_id=tenant_id, subject_id=subject_id), at
        )
        return any(
            _matches_grant(g, scope=scope, resource_id=resource_id) for g in active
        )

    async def resolve_scope(
        self,
        subject_id: str,
        tenant_id: str,
        at: datetime,
    ) -> frozenset[ConsentScope]:
        active = _project_active_grants(
            self._events_for(tenant_id=tenant_id, subject_id=subject_id), at
        )
        return frozenset(g.scope.consent_scope for g in active)

    async def issue_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        scope: ConsentScope,
        resource_id: str,
        granted_by: str,
        source: ConsentGrantSource,
        granted_at: datetime | None = None,
        supersedes_grant_id: str | None = None,
    ) -> ConsentGrantRecord:
        record = ConsentGrantRecord(
            grant_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            subject_id=subject_id,
            scope=ConsentScopeDescriptor(
                consent_scope=scope, resource_id=resource_id
            ),
            event_type=ConsentGrantEventType.GRANT,
            granted_at=granted_at or _utcnow(),
            granted_by=granted_by,
            supersedes_grant_id=supersedes_grant_id,
            source=source,
        )
        self._events.append(record)
        return record

    async def revoke_grant(
        self,
        grant_id: str,
        *,
        revoked_by: str,
        revoke_reason_code: str,
        revoked_at: datetime | None = None,
    ) -> ConsentGrantRecord | None:
        revoked_at = revoked_at or _utcnow()
        original = next((e for e in self._events if e.grant_id == grant_id), None)
        if original is None or original.event_type != ConsentGrantEventType.GRANT:
            return None
        revoke_event = ConsentGrantRecord(
            grant_id=str(uuid.uuid4()),
            tenant_id=original.tenant_id,
            subject_id=original.subject_id,
            scope=original.scope,
            event_type=ConsentGrantEventType.REVOKE,
            granted_at=original.granted_at,
            granted_by=original.granted_by,
            revoked_at=revoked_at,
            revoked_by=revoked_by,
            revoke_reason_code=revoke_reason_code,
            supersedes_grant_id=grant_id,
        )
        self._events.append(revoke_event)
        return revoke_event


class PostgresConsentGrantStore:
    """Postgres-backed append-only consent grant history."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def _fetch_events(self, *, tenant_id: str, subject_id: str) -> list[ConsentGrantRecord]:
        rows = (
            self._s.execute(
                select(ConsentGrantRow).where(
                    ConsentGrantRow.tenant_id == tenant_id,
                    ConsentGrantRow.subject_id == subject_id,
                )
            )
            .scalars()
            .all()
        )
        return [_row_to_record(r) for r in rows]

    async def has_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        scope: ConsentScope,
        resource_id: str,
        at: datetime | None = None,
    ) -> bool:
        at = at or _utcnow()
        active = _project_active_grants(
            self._fetch_events(tenant_id=tenant_id, subject_id=subject_id), at
        )
        return any(
            _matches_grant(g, scope=scope, resource_id=resource_id) for g in active
        )

    async def resolve_scope(
        self,
        subject_id: str,
        tenant_id: str,
        at: datetime,
    ) -> frozenset[ConsentScope]:
        active = _project_active_grants(
            self._fetch_events(tenant_id=tenant_id, subject_id=subject_id), at
        )
        return frozenset(g.scope.consent_scope for g in active)

    async def issue_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        scope: ConsentScope,
        resource_id: str,
        granted_by: str,
        source: ConsentGrantSource,
        granted_at: datetime | None = None,
        supersedes_grant_id: str | None = None,
    ) -> ConsentGrantRecord:
        granted_at = granted_at or _utcnow()
        grant_id = str(uuid.uuid4())
        row = ConsentGrantRow(
            grant_id=grant_id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            scope=_scope_dict(scope, resource_id),
            event_type=ConsentGrantEventType.GRANT.value,
            granted_at=granted_at,
            granted_by=granted_by,
            supersedes_grant_id=supersedes_grant_id,
            source=source.value,
        )
        self._s.add(row)
        self._s.commit()
        return _row_to_record(row)

    async def revoke_grant(
        self,
        grant_id: str,
        *,
        revoked_by: str,
        revoke_reason_code: str,
        revoked_at: datetime | None = None,
    ) -> ConsentGrantRecord | None:
        revoked_at = revoked_at or _utcnow()
        original = self._s.get(ConsentGrantRow, grant_id)
        if original is None or original.event_type != ConsentGrantEventType.GRANT.value:
            return None
        revoke_id = str(uuid.uuid4())
        row = ConsentGrantRow(
            grant_id=revoke_id,
            tenant_id=original.tenant_id,
            subject_id=original.subject_id,
            scope=original.scope,
            event_type=ConsentGrantEventType.REVOKE.value,
            granted_at=original.granted_at,
            granted_by=original.granted_by,
            revoked_at=revoked_at,
            revoked_by=revoked_by,
            revoke_reason_code=revoke_reason_code,
            supersedes_grant_id=grant_id,
        )
        self._s.add(row)
        self._s.commit()
        return _row_to_record(row)

    def event_count(self) -> int:
        """Total history rows — revocation must append, not remove."""
        return self._s.execute(
            select(func.count()).select_from(ConsentGrantRow)
        ).scalar_one()
