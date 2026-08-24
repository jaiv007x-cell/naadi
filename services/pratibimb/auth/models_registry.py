"""
Auth-side ORM rows for tenant JWT registry.
Admin tooling writes these; the ledger read service only reads.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    func,
    text,
)

from services.pratibimb.ledger.models import Base


class TenantRow(Base):
    __tablename__ = "tenants"

    tenant_id = Column(String(64), primary_key=True)
    display_name = Column(String(200), nullable=False)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'deleted')",
            name="ck_tenants_status",
        ),
    )


class TenantIssuerRow(Base):
    """
    (tenant_id, issuer) is unique per row. Active issuer strings are globally
    unique via partial index — the DB-level cross-tenant key-confusion block.
    """

    __tablename__ = "tenant_issuers"

    tenant_id = Column(
        String(64),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    issuer = Column(String(512), primary_key=True)
    jwks_url = Column(String(1024), nullable=False)
    jwks_host_override = Column(String(255), nullable=True)
    audiences = Column(JSON, nullable=False, default=list)
    algorithms = Column(JSON, nullable=False, default=lambda: ["RS256"])
    kid_pin = Column(JSON, nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    retirement_grace_s = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    notes = Column(String(1024), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "jwks_url LIKE 'https://%'",
            name="ck_tenant_issuers_jwks_https",
        ),
        CheckConstraint(
            "retirement_grace_s >= 0",
            name="ck_tenant_issuers_grace_nonneg",
        ),
        Index(
            "ux_tenant_issuers_issuer_unique_active",
            "issuer",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
            sqlite_where=text("retired_at IS NULL"),
        ),
    )
