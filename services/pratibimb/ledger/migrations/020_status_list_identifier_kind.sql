-- 020_status_list_identifier_kind.sql
-- I.b: normalized status-list identifier registry with closed identifier_kind
-- CHECK + UNIQUE (tenant_id, identifier_kind, identifier_id).
--
-- Apply BEFORE deploying I.b.1 status-list publishers that write arp/regrade
-- kinds or emit status_list.v2 envelopes. Same apply-in-prod-before-deploy
-- discipline as 015/017/019.
--
-- Backfill: existing credential_ledger revocations → identifier_kind='credential'
-- (non-null; no read-path default). Historical snapshot envelope_bytes stay as
-- written; new publishes use status_list.v2 with identifier_kind required.

BEGIN;

CREATE TABLE IF NOT EXISTS status_list_identifier (
    tenant_id         TEXT        NOT NULL,
    identifier_kind   TEXT        NOT NULL,
    identifier_id     TEXT        NOT NULL,
    revoked_at        TIMESTAMPTZ NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, identifier_kind, identifier_id),
    CONSTRAINT ck_status_list_identifier_kind
      CHECK (identifier_kind IN ('credential', 'regrade', 'arp'))
);

CREATE INDEX IF NOT EXISTS ix_status_list_identifier_tenant_revoked
    ON status_list_identifier (tenant_id, revoked_at ASC);

-- Backfill revoked credentials (latest revoked_at per credential_id).
INSERT INTO status_list_identifier (
    tenant_id, identifier_kind, identifier_id, revoked_at, created_at
)
SELECT
    tenant_id,
    'credential',
    credential_id,
    MAX(revoked_at),
    MAX(revoked_at)
FROM credential_ledger
WHERE revoked_at IS NOT NULL
GROUP BY tenant_id, credential_id
ON CONFLICT (tenant_id, identifier_kind, identifier_id) DO NOTHING;

COMMIT;
