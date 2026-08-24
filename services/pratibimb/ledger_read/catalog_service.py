"""E3: authoring catalog reads — published_case_versions SoT (not corpus)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.schemas import PublishedCaseVersionView, RetiredCaseVersionView
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.ledger_read.catalog_cursor import decode_catalog_cursor, encode_catalog_cursor
from services.pratibimb.ledger_read.errors import InvalidCatalogCursorError, TenantBoundaryError

CATALOG_PAGE_MAX = 100

T = TypeVar("T")


@dataclass(frozen=True)
class CatalogPageResult(Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def _row_to_view(row) -> PublishedCaseVersionView:
    return PublishedCaseVersionView(
        id=row.id,
        draft_id=row.draft_id,
        tenant_id=row.tenant_id,
        case_id=row.case_id,
        version=row.version,
        assessment_mode=row.assessment_mode,
        content_hash=row.content_hash,
        harness_version=row.harness_version,
        published_at=row.published_at,
        published_by=row.published_by,
        clinical_reviewer=row.clinical_reviewer,
        retired_at=row.retired_at,
        retired_by=row.retired_by,
        retired_reason_code=row.retired_reason_code,
        retired_reason_note=row.retired_reason_text,
    )


def _retired_to_view(row) -> RetiredCaseVersionView:
    return RetiredCaseVersionView(
        id=row.id,
        published_case_version_id=row.published_case_version_id,
        draft_id=row.draft_id,
        tenant_id=row.tenant_id,
        case_id=row.case_id,
        version=row.version,
        retired_at=row.retired_at,
        retired_by=row.retired_by,
        reason_code=row.reason_code,
        reason_note=row.reason_text,
    )


@dataclass(frozen=True)
class PublishedCaseVersionsFilter:
    tenant_id: str
    case_id: str
    include_retired: bool = True
    limit: int = CATALOG_PAGE_MAX
    cursor: str | None = None


@dataclass(frozen=True)
class RetirementHistoryFilter:
    tenant_id: str
    case_id: str | None = None
    limit: int = CATALOG_PAGE_MAX
    cursor: str | None = None


def _decode_page_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if cursor is None:
        return None, None
    at, row_id = decode_catalog_cursor(cursor)
    return at, row_id


def _page_from_rows(rows, *, limit: int, at_attr: str) -> CatalogPageResult:
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = page_rows
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_catalog_cursor(
            at=getattr(last, at_attr),
            row_id=last.id,
        )
    return CatalogPageResult(items=items, next_cursor=next_cursor)


class AuthoringCatalogReadService:
    """Reads published_case_versions directly — I-E3-6: no corpus projection."""

    def __init__(self, store: CaseDraftStore) -> None:
        self._store = store

    @staticmethod
    def _assert_tenant(auth: AuthContext, tenant_id: str) -> None:
        if tenant_id != auth.tenant_id:
            raise TenantBoundaryError(
                requested=tenant_id,
                auth_tenant=auth.tenant_id,
            )

    def get_published_case_versions(
        self,
        filt: PublishedCaseVersionsFilter,
        *,
        auth: AuthContext,
    ) -> CatalogPageResult[PublishedCaseVersionView]:
        # I-E3-6: published_case_versions table only — never runtime.published_case_corpus.
        self._assert_tenant(auth, filt.tenant_id)
        if filt.limit < 1 or filt.limit > CATALOG_PAGE_MAX:
            raise ValueError(f"limit must be 1..{CATALOG_PAGE_MAX}")
        try:
            after_at, after_id = _decode_page_cursor(filt.cursor)
        except InvalidCatalogCursorError:
            raise
        fetch = filt.limit + 1
        rows = self._store.list_published_for_tenant_case_page(
            tenant_id=filt.tenant_id,
            case_id=filt.case_id,
            include_retired=filt.include_retired,
            limit=fetch,
            after_published_at=after_at,
            after_id=after_id,
        )
        paged = _page_from_rows(rows, limit=filt.limit, at_attr="published_at")
        return CatalogPageResult(
            items=[_row_to_view(row) for row in paged.items],
            next_cursor=paged.next_cursor,
        )

    def get_published_case_version(
        self,
        *,
        tenant_id: str,
        case_id: str,
        version: str,
        auth: AuthContext,
    ) -> PublishedCaseVersionView | None:
        # I-E3-6: published_case_versions table only — never runtime.published_case_corpus.
        self._assert_tenant(auth, tenant_id)
        row = self._store.get_published_version(
            tenant_id=tenant_id,
            case_id=case_id,
            version=version,
        )
        if row is None:
            return None
        return _row_to_view(row)

    def get_retirement_history(
        self,
        filt: RetirementHistoryFilter,
        *,
        auth: AuthContext,
    ) -> CatalogPageResult[RetiredCaseVersionView]:
        # I-E3-6: retired_case_versions table only — never runtime.published_case_corpus.
        self._assert_tenant(auth, filt.tenant_id)
        if filt.limit < 1 or filt.limit > CATALOG_PAGE_MAX:
            raise ValueError(f"limit must be 1..{CATALOG_PAGE_MAX}")
        try:
            after_at, after_id = _decode_page_cursor(filt.cursor)
        except InvalidCatalogCursorError:
            raise
        fetch = filt.limit + 1
        rows = self._store.list_retirement_history_for_tenant(
            tenant_id=filt.tenant_id,
            case_id=filt.case_id,
            limit=fetch,
            after_retired_at=after_at,
            after_id=after_id,
        )
        paged = _page_from_rows(rows, limit=filt.limit, at_attr="retired_at")
        return CatalogPageResult(
            items=[_retired_to_view(row) for row in paged.items],
            next_cursor=paged.next_cursor,
        )
