-- Append-only consent grant history for ledger read authorization.
-- Revocation appends a new row with supersedes_grant_id; rows are never updated or deleted.

CREATE TABLE IF NOT EXISTS consent_grant (
    grant_id VARCHAR NOT NULL PRIMARY KEY,
    subject_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    scope JSONB NOT NULL,
    event_type VARCHAR NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL,
    granted_by VARCHAR NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by VARCHAR,
    revoke_reason_code VARCHAR,
    supersedes_grant_id VARCHAR,
    source VARCHAR
);

CREATE INDEX IF NOT EXISTS ix_consent_grant_tenant_subject
    ON consent_grant (tenant_id, subject_id);

CREATE INDEX IF NOT EXISTS ix_consent_grant_granted_at
    ON consent_grant (granted_at);

CREATE INDEX IF NOT EXISTS ix_consent_grant_supersedes
    ON consent_grant (supersedes_grant_id);
