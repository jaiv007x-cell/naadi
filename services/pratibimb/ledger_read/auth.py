"""
Consent resolution — grant store is authoritative; JWT carries identity only.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.consent_store import ConsentGrantStore
from services.pratibimb.ledger_read.errors import ScopeDeniedError
from shared.schemas.ledger_read import ConsentScope


class ConsentResolver:
    """Resolve effective ConsentScope from server-side grant store at request time."""

    def __init__(self, store: ConsentGrantStore) -> None:
        self.store = store

    async def _granted_scopes(
        self, auth: AuthContext, *, at: datetime
    ) -> frozenset[ConsentScope]:
        return await self.store.resolve_scope(
            auth.subject_pseudo_id,
            auth.tenant_id,
            at,
        )

    async def for_learner(
        self,
        auth: AuthContext,
        learner_pseudo_id: str,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        if auth.subject_pseudo_id == learner_pseudo_id:
            if await self.store.has_grant(
                tenant_id=auth.tenant_id,
                subject_id=auth.subject_pseudo_id,
                scope=ConsentScope.SELF_LEARNER,
                resource_id=learner_pseudo_id,
                at=at,
            ):
                return ConsentScope.SELF_LEARNER
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=ConsentScope.PRECEPTOR_REVIEW,
            resource_id=learner_pseudo_id,
            at=at,
        ):
            return ConsentScope.PRECEPTOR_REVIEW
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(
            required=frozenset({
                ConsentScope.SELF_LEARNER,
                ConsentScope.PRECEPTOR_REVIEW,
            }),
            granted=granted,
        )

    async def for_aggregate(
        self,
        auth: AuthContext,
        *,
        resource_id: str = "aggregate",
        research: bool = False,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = (
            ConsentScope.RESEARCH_DEIDENTIFIED
            if research
            else ConsentScope.AGGREGATE_ANALYTICS
        )
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id=resource_id,
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_authoring_read_published(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.AUTHORING_READ_PUBLISHED
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_authoring_read_retirement_history(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.AUTHORING_READ_RETIREMENT_HISTORY
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_issue_credential(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_ISSUE_CREDENTIAL
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_fetch_evidence_by_ref(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_FETCH_EVIDENCE_BY_REF
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_verify_credential(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_VERIFY_CREDENTIAL
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_fetch_status_list(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_FETCH_STATUS_LIST
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_revoke_credential(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_REVOKE_CREDENTIAL
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_regrade_session(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_REGRADE_SESSION
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_verify_regrade(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_VERIFY_REGRADE
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_recompute_alternate_rubric(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_verify_arp(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_VERIFY_ARP
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_read_session_evidence(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_READ_SESSION_EVIDENCE
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)

    async def for_ncvet_read_learner_sessions(
        self,
        auth: AuthContext,
        *,
        at: datetime | None = None,
    ) -> ConsentScope:
        at = at or datetime.now(timezone.utc)
        scope = ConsentScope.NCVET_READ_LEARNER_SESSIONS
        if await self.store.has_grant(
            tenant_id=auth.tenant_id,
            subject_id=auth.subject_pseudo_id,
            scope=scope,
            resource_id="*",
            at=at,
        ):
            return scope
        granted = await self._granted_scopes(auth, at=at)
        raise ScopeDeniedError(required=frozenset({scope}), granted=granted)
