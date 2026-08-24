"""
Tenant-scoped JWKS resolution.

Maps (tenant_id, iss) → validated JWKS URL. Enforces tenant/issuer binding
before signature verification to block cross-tenant key confusion.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from services.pratibimb.auth.jwks_cache import JwksCache, JwksFetchError
from services.pratibimb.auth.startup import is_production_auth_mode
from services.pratibimb.auth.models_registry import TenantIssuerRow, TenantRow

log = logging.getLogger(__name__)


class TenantResolutionError(Exception):
    """Base — maps to HTTP 401 at the edge."""


class UnknownTenantError(TenantResolutionError):
    pass


class SuspendedTenantError(TenantResolutionError):
    pass


class UnknownIssuerError(TenantResolutionError):
    pass


class IssuerTenantMismatchError(TenantResolutionError):
    pass


class IssuerRetiredError(TenantResolutionError):
    pass


class JwksUrlPolicyError(TenantResolutionError):
    pass


@dataclass(frozen=True)
class ResolvedIssuer:
    tenant_id: str
    issuer: str
    jwks_url: str
    audiences: frozenset[str]
    algorithms: frozenset[str]
    retired_at: Optional[datetime] = None
    retirement_grace_s: int = 0
    kid_pin: Optional[frozenset[str]] = None
    jwks_host_override: Optional[str] = None


class TenantIssuerRegistry(Protocol):
    def resolve(self, tenant_id: str, issuer: str) -> ResolvedIssuer: ...


class SqlTenantIssuerRegistry:
    """Postgres-backed registry — read-only from this service."""

    def __init__(self, db: DbSession) -> None:
        self.db = db

    def resolve(self, tenant_id: str, issuer: str) -> ResolvedIssuer:
        tenant = self.db.execute(
            select(TenantRow).where(TenantRow.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if tenant is None:
            log.info("tenant_jwks.unknown_tenant tenant=%s", tenant_id)
            raise UnknownTenantError()
        if tenant.status != "active":
            log.info(
                "tenant_jwks.suspended_tenant tenant=%s status=%s",
                tenant_id,
                tenant.status,
            )
            raise SuspendedTenantError()

        row = self.db.execute(
            select(TenantIssuerRow).where(
                TenantIssuerRow.tenant_id == tenant_id,
                TenantIssuerRow.issuer == issuer,
            )
        ).scalar_one_or_none()
        if row is None:
            cross = self.db.execute(
                select(TenantIssuerRow.tenant_id)
                .where(
                    TenantIssuerRow.issuer == issuer,
                    TenantIssuerRow.retired_at.is_(None),
                )
                .limit(1)
            ).scalar_one_or_none()
            if cross is not None:
                log.warning(
                    "tenant_jwks.cross_tenant_attempt presented_tenant=%s "
                    "issuer=%s registered_under=%s",
                    tenant_id,
                    issuer,
                    cross,
                )
                raise IssuerTenantMismatchError()
            log.info("tenant_jwks.unknown_issuer tenant=%s issuer=%s", tenant_id, issuer)
            raise UnknownIssuerError()

        _validate_jwks_url_policy(issuer, row.jwks_url, row.jwks_host_override)

        return ResolvedIssuer(
            tenant_id=tenant_id,
            issuer=issuer,
            jwks_url=row.jwks_url,
            audiences=frozenset(row.audiences or ()),
            algorithms=frozenset(row.algorithms or ("RS256",)),
            retired_at=row.retired_at,
            retirement_grace_s=int(row.retirement_grace_s or 0),
            kid_pin=frozenset(row.kid_pin) if row.kid_pin else None,
            jwks_host_override=row.jwks_host_override,
        )


class InMemoryTenantIssuerRegistry:
    """Test/dev registry. Refuses use in AUTH_MODE=production."""

    def __init__(
        self,
        bindings: dict[tuple[str, str], ResolvedIssuer],
        active_tenants: frozenset[str],
    ) -> None:
        self._bindings = dict(bindings)
        self._active = frozenset(active_tenants)

    def resolve(self, tenant_id: str, issuer: str) -> ResolvedIssuer:
        if is_production_auth_mode():
            raise RuntimeError(
                "InMemoryTenantIssuerRegistry is forbidden in AUTH_MODE=production"
            )
        if tenant_id not in self._active:
            raise UnknownTenantError()
        resolved = self._bindings.get((tenant_id, issuer))
        if resolved is None:
            for (t, i), _ in self._bindings.items():
                if i == issuer and t != tenant_id:
                    raise IssuerTenantMismatchError()
            raise UnknownIssuerError()
        _validate_jwks_url_policy(
            resolved.issuer, resolved.jwks_url, resolved.jwks_host_override
        )
        return resolved


def _validate_jwks_url_policy(
    issuer: str, jwks_url: str, host_override: Optional[str]
) -> None:
    u = urlparse(jwks_url)
    if u.scheme != "https":
        raise JwksUrlPolicyError()
    if not u.netloc:
        raise JwksUrlPolicyError()

    expected_host = host_override or urlparse(issuer).netloc
    if not expected_host:
        raise JwksUrlPolicyError()
    if u.netloc.lower() != expected_host.lower():
        log.warning(
            "tenant_jwks.host_mismatch issuer_host=%s jwks_host=%s override=%s",
            expected_host,
            u.netloc,
            host_override,
        )
        raise JwksUrlPolicyError()


def enforce_retirement(
    resolved: ResolvedIssuer,
    token_iat: datetime,
    now: Optional[datetime] = None,
) -> None:
    if resolved.retired_at is None:
        return
    now = now or datetime.now(timezone.utc)
    grace = timedelta(seconds=resolved.retirement_grace_s)
    cutoff = resolved.retired_at + grace
    if token_iat > resolved.retired_at:
        log.info(
            "tenant_jwks.retired_issuer_post_retirement_iat tenant=%s issuer=%s",
            resolved.tenant_id,
            resolved.issuer,
        )
        raise IssuerRetiredError()
    if now > cutoff:
        log.info(
            "tenant_jwks.retired_issuer_grace_expired tenant=%s issuer=%s",
            resolved.tenant_id,
            resolved.issuer,
        )
        raise IssuerRetiredError()


class TenantJwks:
    """Registry lookup → URL policy → JwksCache fetch → kid pinning."""

    def __init__(self, registry: TenantIssuerRegistry, cache: JwksCache) -> None:
        self.registry = registry
        self.cache = cache

    def resolve(self, tenant_id: str, issuer: str) -> ResolvedIssuer:
        return self.registry.resolve(tenant_id, issuer)

    def keys_for(
        self, tenant_id: str, issuer: str, kid: Optional[str]
    ) -> tuple[list[dict], ResolvedIssuer]:
        resolved = self.registry.resolve(tenant_id, issuer)

        if kid is not None and resolved.kid_pin is not None:
            if kid not in resolved.kid_pin:
                log.warning(
                    "tenant_jwks.kid_not_pinned tenant=%s issuer=%s kid=%s",
                    tenant_id,
                    issuer,
                    kid,
                )
                raise UnknownIssuerError()

        try:
            jwks = self.cache.get(resolved.jwks_url)
        except JwksFetchError:
            log.warning(
                "tenant_jwks.jwks_fetch_failed tenant=%s issuer=%s url=%s",
                tenant_id,
                issuer,
                resolved.jwks_url,
            )
            raise UnknownIssuerError() from None

        keys = jwks.get("keys", [])
        if kid is not None:
            keys = [k for k in keys if k.get("kid") == kid]
            if not keys:
                log.info(
                    "tenant_jwks.kid_not_in_jwks tenant=%s issuer=%s kid=%s",
                    tenant_id,
                    issuer,
                    kid,
                )
                raise UnknownIssuerError()

        keys = [
            k
            for k in keys
            if k.get("alg", "RS256") in resolved.algorithms or k.get("alg") is None
        ]
        if not keys:
            raise UnknownIssuerError()

        return keys, resolved


def registry_from_env() -> InMemoryTenantIssuerRegistry:
    """Build dev/test registry from TENANT_TRUST_CONFIG env."""
    bindings: dict[tuple[str, str], ResolvedIssuer] = {}
    active: set[str] = set()
    for entry in os.environ.get("TENANT_TRUST_CONFIG", "").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = dict(kv.split("=", 1) for kv in entry.split(",") if "=" in kv)
        tenant = parts["tenant"]
        iss = parts["iss"]
        jwks = parts.get("jwks", f"{iss.rstrip('/')}/.well-known/jwks.json")
        aud_raw = parts.get("aud", "pratibimb-ledger-read")
        audiences = frozenset(a.strip() for a in aud_raw.split("|") if a.strip())
        active.add(tenant)
        bindings[(tenant, iss)] = ResolvedIssuer(
            tenant_id=tenant,
            issuer=iss,
            jwks_url=jwks,
            audiences=audiences,
            algorithms=frozenset({"RS256"}),
        )
    return InMemoryTenantIssuerRegistry(bindings, frozenset(active))
