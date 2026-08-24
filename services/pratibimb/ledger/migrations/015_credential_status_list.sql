-- 015_credential_status_list.sql
-- G.b: ncvet_verifier caller_kind + append-only credential status-list snapshots.
--
-- Apply BEFORE deploying verify / status_list / revoke routes that emit
-- caller_kind='ncvet_verifier'. Same apply-in-prod-before-deploy discipline as 012/014.

BEGIN;

ALTER TABLE ledger_read_audit
  DROP CONSTRAINT IF EXISTS ck_audit_caller_kind;

ALTER TABLE ledger_read_audit
  ADD CONSTRAINT ck_audit_caller_kind
  CHECK (caller_kind IN (
    'preceptor', 'analyst', 'service', 'authoring',
    'ncvet_audit', 'ncvet_issuer', 'ncvet_verifier'
  ));

CREATE TABLE IF NOT EXISTS credential_status_list_snapshot (
    snapshot_id                 TEXT        PRIMARY KEY,
    tenant_id                   TEXT        NOT NULL,
    status_list_schema_version  TEXT        NOT NULL,
    key_id                      TEXT        NOT NULL,
    signed_at                   TIMESTAMPTZ NOT NULL,
    valid_until                 TIMESTAMPTZ NOT NULL,
    envelope_bytes              TEXT        NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_status_list_valid_until_after_signed
      CHECK (valid_until > signed_at)
);

CREATE INDEX IF NOT EXISTS ix_status_list_tenant_signed
    ON credential_status_list_snapshot (tenant_id, signed_at DESC);

COMMIT;
