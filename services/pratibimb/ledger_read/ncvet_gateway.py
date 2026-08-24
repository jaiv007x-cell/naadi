"""JWT gateway for NCVET session evidence reads."""
from __future__ import annotations

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.errors import (
    InvalidNcvetCursorError,
    ScopeDeniedError,
    TenantBoundaryError,
)
from services.pratibimb.ledger_read.ncvet_audit import (
    AuditingNcvetReadService,
    ncvet_list_audit_params,
)
from services.pratibimb.ledger_read.ncvet_cursor import NCVET_LIST_PAGE_MAX
from services.pratibimb.ledger_read.ncvet_service import (
    LearnerSessionsFilter,
    NcvetListPageResult,
    resolve_list_page_ordinal,
)
from shared.schemas.ledger_read import NcvetSessionEvidenceView


class JwtNcvetGateway:
    def __init__(
        self,
        service: AuditingNcvetReadService,
        consent: ConsentResolver,
    ) -> None:
        self.service = service
        self.consent = consent

    async def get_session_evidence(
        self,
        auth: AuthContext,
        session_id: str,
        *,
        tenant_id: str | None = None,
        request_id: str | None = None,
    ) -> NcvetSessionEvidenceView:
        effective_tenant = tenant_id or auth.tenant_id
        params = {"session_id": session_id, "tenant_id": effective_tenant}
        if effective_tenant != auth.tenant_id:
            self.service.record_tenant_denial(
                auth,
                "get_session_evidence",
                params,
                request_id=request_id,
            )
            raise TenantBoundaryError(
                requested=effective_tenant,
                auth_tenant=auth.tenant_id,
            )
        try:
            scope = await self.consent.for_ncvet_read_session_evidence(auth)
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth,
                "get_session_evidence",
                params,
                request_id=request_id,
            )
            raise
        return await self.service.get_session_evidence(
            session_id,
            scope=scope,
            auth=auth,
            tenant_id=tenant_id,
            request_id=request_id,
        )

    async def list_learner_sessions(
        self,
        auth: AuthContext,
        learner_pseudo_id: str,
        *,
        tenant_id: str | None = None,
        limit: int = NCVET_LIST_PAGE_MAX,
        cursor: str | None = None,
        request_id: str | None = None,
    ) -> NcvetListPageResult:
        effective_tenant = tenant_id or auth.tenant_id
        page_ordinal = 1
        if cursor is not None:
            try:
                page_ordinal, _ = resolve_list_page_ordinal(
                    cursor,
                    learner_pseudo_id=learner_pseudo_id,
                )
            except InvalidNcvetCursorError:
                params = ncvet_list_audit_params(
                    tenant_id=effective_tenant,
                    learner_pseudo_id=learner_pseudo_id,
                    limit=limit,
                    cursor=cursor,
                    page_ordinal=0,
                )
                self.service.record_cursor_invalid(
                    auth,
                    "list_learner_sessions",
                    params,
                    request_id=request_id,
                )
                raise
        params = ncvet_list_audit_params(
            tenant_id=effective_tenant,
            learner_pseudo_id=learner_pseudo_id,
            limit=limit,
            cursor=cursor,
            page_ordinal=page_ordinal,
        )
        if effective_tenant != auth.tenant_id:
            self.service.record_tenant_denial(
                auth,
                "list_learner_sessions",
                params,
                request_id=request_id,
            )
            raise TenantBoundaryError(
                requested=effective_tenant,
                auth_tenant=auth.tenant_id,
            )
        try:
            scope = await self.consent.for_ncvet_read_learner_sessions(auth)
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth,
                "list_learner_sessions",
                params,
                request_id=request_id,
            )
            raise
        filt = LearnerSessionsFilter(
            tenant_id=effective_tenant,
            learner_pseudo_id=learner_pseudo_id,
            limit=limit,
            cursor=cursor,
        )
        return await self.service.list_learner_sessions(
            filt,
            scope=scope,
            auth=auth,
            request_id=request_id,
        )
