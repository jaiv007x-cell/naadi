-- 007_ledger_read_audit_fingerprint.sql
--
-- Adds tamper-evidence to successful audit rows. NULL on denial by design:
-- a denial has no returned result to bind, and the outcome column already
-- distinguishes the two states. Do NOT backfill; historical rows stay NULL.

BEGIN;

ALTER TABLE ledger_read_audit
    ADD COLUMN IF NOT EXISTS result_fingerprint TEXT;

COMMENT ON COLUMN ledger_read_audit.result_fingerprint IS
    'SHA-256 hex of canonical JSON of the response body. '
    'NULL for denial rows (no result bound). '
    'NULL for pre-migration rows (not backfilled).';

CREATE INDEX IF NOT EXISTS ix_ledger_read_audit_fingerprint
    ON ledger_read_audit (result_fingerprint)
    WHERE result_fingerprint IS NOT NULL;

COMMIT;
