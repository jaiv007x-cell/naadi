"""JWT-authenticated gateway over AuditingAuthoringCatalogReadService."""
from __future__ import annotations

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.schemas import PublishedCaseVersionView, RetiredCaseVersionView
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.catalog_audit import AuditingAuthoringCatalogReadService
from services.pratibimb.ledger_read.catalog_service import (
    CatalogPageResult,
    PublishedCaseVersionsFilter,
    RetirementHistoryFilter,
)
from services.pratibimb.ledger_read.errors import (
    InvalidCatalogCursorError,
    ScopeDeniedError,
    TenantBoundaryError,
)


class JwtAuthoringCatalogGateway:
    def __init__(
        self,
        service: AuditingAuthoringCatalogReadService,
        consent: ConsentResolver,
    ) -> None:
        self.service = service
        self.consent = consent

    async def list_published_case_versions(
        self,
        auth: AuthContext,
        *,
        case_id: str,
        include_retired: bool = True,
        tenant_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        request_id: str | None = None,
    ) -> CatalogPageResult[PublishedCaseVersionView]:
        effective_tenant = tenant_id or auth.tenant_id
        filt = PublishedCaseVersionsFilter(
            tenant_id=effective_tenant,
            case_id=case_id,
            include_retired=include_retired,
            limit=limit,
            cursor=cursor,
        )
        params = {
            "tenant_id": effective_tenant,
            "case_id": case_id,
            "include_retired": include_retired,
            "limit": limit,
            "cursor": cursor,
        }
        if effective_tenant != auth.tenant_id:
            self.service.record_tenant_denial(
                auth,
                "get_published_case_versions",
                params,
                request_id=request_id,
            )
            raise TenantBoundaryError(
                requested=effective_tenant,
                auth_tenant=auth.tenant_id,
            )
        if cursor is not None:
            try:
                from services.pratibimb.ledger_read.catalog_cursor import decode_catalog_cursor

                decode_catalog_cursor(cursor)
            except InvalidCatalogCursorError:
                self.service.record_cursor_invalid(
                    auth,
                    "get_published_case_versions",
                    params,
                    request_id=request_id,
                )
                raise
        try:
            scope = await self.consent.for_authoring_read_published(auth)
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth,
                "get_published_case_versions",
                params,
                request_id=request_id,
            )
            raise
        return await self.service.get_published_case_versions(
            filt,
            scope=scope,
            auth=auth,
            request_id=request_id,
        )

    async def get_published_case_version(
        self,
        auth: AuthContext,
        *,
        case_id: str,
        version: str,
        tenant_id: str | None = None,
        request_id: str | None = None,
    ) -> PublishedCaseVersionView | None:
        effective_tenant = tenant_id or auth.tenant_id
        params = {
            "tenant_id": effective_tenant,
            "case_id": case_id,
            "version": version,
        }
        if effective_tenant != auth.tenant_id:
            self.service.record_tenant_denial(
                auth,
                "get_published_case_versions",
                params,
                request_id=request_id,
            )
            raise TenantBoundaryError(
                requested=effective_tenant,
                auth_tenant=auth.tenant_id,
            )
        try:
            scope = await self.consent.for_authoring_read_published(auth)
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth,
                "get_published_case_versions",
                params,
                request_id=request_id,
            )
            raise
        return await self.service.get_published_case_version(
            tenant_id=effective_tenant,
            case_id=case_id,
            version=version,
            scope=scope,
            auth=auth,
            request_id=request_id,
        )

    async def list_retirement_history(
        self,
        auth: AuthContext,
        *,
        case_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        request_id: str | None = None,
    ) -> CatalogPageResult[RetiredCaseVersionView]:
        effective_tenant = tenant_id or auth.tenant_id
        filt = RetirementHistoryFilter(
            tenant_id=effective_tenant,
            case_id=case_id,
            limit=limit,
            cursor=cursor,
        )
        params = {
            "tenant_id": effective_tenant,
            "case_id": case_id,
            "limit": limit,
            "cursor": cursor,
        }
        if effective_tenant != auth.tenant_id:
            self.service.record_tenant_denial(
                auth,
                "get_retirement_history",
                params,
                request_id=request_id,
            )
            raise TenantBoundaryError(
                requested=effective_tenant,
                auth_tenant=auth.tenant_id,
            )
        if cursor is not None:
            try:
                from services.pratibimb.ledger_read.catalog_cursor import decode_catalog_cursor

                decode_catalog_cursor(cursor)
            except InvalidCatalogCursorError:
                self.service.record_cursor_invalid(
                    auth,
                    "get_retirement_history",
                    params,
                    request_id=request_id,
                )
                raise
        try:
            scope = await self.consent.for_authoring_read_retirement_history(auth)
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth,
                "get_retirement_history",
                params,
                request_id=request_id,
            )
            raise
        return await self.service.get_retirement_history(
            filt,
            scope=scope,
            auth=auth,
            request_id=request_id,
        )
