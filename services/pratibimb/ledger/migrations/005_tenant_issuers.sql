-- Tenant registry for multi-tenant JWT verification (issuer → JWKS binding).
-- Active issuer strings are globally unique (partial index).

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR(64) NOT NULL PRIMARY KEY,
    display_name VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_tenants_status CHECK (status IN ('active', 'suspended', 'deleted'))
);

CREATE TABLE IF NOT EXISTS tenant_issuers (
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    issuer VARCHAR(512) NOT NULL,
    jwks_url VARCHAR(1024) NOT NULL,
    jwks_host_override VARCHAR(255),
    audiences JSONB NOT NULL DEFAULT '[]',
    algorithms JSONB NOT NULL DEFAULT '["RS256"]',
    kid_pin JSONB,
    retired_at TIMESTAMPTZ,
    retirement_grace_s INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes VARCHAR(1024),
    PRIMARY KEY (tenant_id, issuer),
    CONSTRAINT ck_tenant_issuers_jwks_https CHECK (jwks_url LIKE 'https://%'),
    CONSTRAINT ck_tenant_issuers_grace_nonneg CHECK (retirement_grace_s >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_tenant_issuers_issuer_unique_active
    ON tenant_issuers (issuer)
    WHERE retired_at IS NULL;
