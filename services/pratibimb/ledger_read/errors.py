"""Ledger read privacy and scope errors."""
from __future__ import annotations

from dataclasses import dataclass

from shared.schemas.ledger_read import ConsentScope


@dataclass
class ScopeDeniedError(Exception):
    required: frozenset[ConsentScope]
    granted: frozenset[ConsentScope]
    error_kind: str = "consent_scope"


class TenantBoundaryError(Exception):
    """Query named a tenant_id that does not match AuthContext.tenant_id."""

    def __init__(self, *, requested: str, auth_tenant: str) -> None:
        self.requested = requested
        self.auth_tenant = auth_tenant
        super().__init__(
            f"tenant boundary: requested={requested!r} auth={auth_tenant!r}"
        )


class InvalidCatalogCursorError(Exception):
    """Malformed or tampered catalog pagination cursor."""

    def __init__(self, *, cursor: str) -> None:
        self.cursor = cursor
        super().__init__(f"invalid catalog cursor: {cursor!r}")


class InvalidNcvetCursorError(Exception):
    """Malformed, tampered, or cross-learner NCVET list cursor."""

    def __init__(self, *, cursor: str) -> None:
        self.cursor = cursor
        super().__init__(f"invalid ncvet cursor: {cursor!r}")


class InsufficientCohortError(Exception):
    """Raised when a query would return fewer than the k-anonymity floor."""

    def __init__(self, *, minimum_required: int) -> None:
        self.minimum_required = minimum_required
        super().__init__("minimum_cohort_size_not_met")

    @property
    def min_k(self) -> int:
        return self.minimum_required
