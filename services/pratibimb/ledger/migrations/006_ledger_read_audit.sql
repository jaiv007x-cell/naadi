-- 006_ledger_read_audit.sql
-- Append-only audit trail for LedgerReadService.
-- Write-only from app role; read-only from auditor role.

BEGIN;

CREATE TABLE IF NOT EXISTS ledger_read_audit (
    query_id                     TEXT        PRIMARY KEY,
    at_utc                       TIMESTAMPTZ NOT NULL,

    tenant_id                    TEXT        NOT NULL,
    subject_pseudo_id            TEXT        NOT NULL,
    caller_kind                  TEXT        NOT NULL,

    scope                        TEXT        NOT NULL,
    query_kind                   TEXT        NOT NULL,
    query_params_hash            TEXT        NOT NULL,
    query_params_bytes           INTEGER     NOT NULL,

    outcome                      TEXT        NOT NULL,
    result_row_count             INTEGER,
    k_anonymity_floor_applied    INTEGER,
    error_kind                   TEXT,

    request_id                   TEXT,
    duration_ms                  INTEGER     NOT NULL,

    CONSTRAINT ck_audit_outcome
      CHECK (outcome IN ('ok','scope_denied','insufficient_cohort','error')),
    CONSTRAINT ck_audit_caller_kind
      CHECK (caller_kind IN ('preceptor','analyst','service')),
    CONSTRAINT ck_audit_row_count_iff_ok
      CHECK (
        (outcome = 'ok'  AND result_row_count IS NOT NULL) OR
        (outcome <> 'ok' AND result_row_count IS NULL)
      )
);

CREATE INDEX IF NOT EXISTS ix_audit_at              ON ledger_read_audit (at_utc);
CREATE INDEX IF NOT EXISTS ix_audit_tenant_id       ON ledger_read_audit (tenant_id);
CREATE INDEX IF NOT EXISTS ix_audit_subject         ON ledger_read_audit (subject_pseudo_id);
CREATE INDEX IF NOT EXISTS ix_audit_request_id      ON ledger_read_audit (request_id);
CREATE INDEX IF NOT EXISTS ix_audit_tenant_at       ON ledger_read_audit (tenant_id, at_utc);
CREATE INDEX IF NOT EXISTS ix_audit_tenant_subj_at  ON ledger_read_audit (tenant_id, subject_pseudo_id, at_utc);

-- Append-only enforcement at the role level (Postgres deployments).
-- SQLite test environments skip role/trigger grants.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pratibimb_app') THEN
    REVOKE ALL ON ledger_read_audit FROM PUBLIC;
    GRANT INSERT, SELECT ON ledger_read_audit TO pratibimb_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pratibimb_auditor') THEN
    GRANT SELECT ON ledger_read_audit TO pratibimb_auditor;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION _audit_block_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'ledger_read_audit is append-only (op=%)', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_no_update ON ledger_read_audit;
CREATE TRIGGER trg_audit_no_update
  BEFORE UPDATE ON ledger_read_audit
  FOR EACH ROW EXECUTE FUNCTION _audit_block_mutation();

DROP TRIGGER IF EXISTS trg_audit_no_delete ON ledger_read_audit;
CREATE TRIGGER trg_audit_no_delete
  BEFORE DELETE ON ledger_read_audit
  FOR EACH ROW EXECUTE FUNCTION _audit_block_mutation();

COMMIT;
