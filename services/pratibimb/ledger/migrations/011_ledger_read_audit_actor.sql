-- 011_ledger_read_audit_actor.sql
-- E3.a: actor_subject_id for catalog reads; caller_kind += authoring.

BEGIN;

ALTER TABLE ledger_read_audit
  ADD COLUMN IF NOT EXISTS actor_subject_id TEXT;

-- Postgres: replace caller_kind CHECK to include authoring.
ALTER TABLE ledger_read_audit
  DROP CONSTRAINT IF EXISTS ck_audit_caller_kind;

ALTER TABLE ledger_read_audit
  ADD CONSTRAINT ck_audit_caller_kind
  CHECK (caller_kind IN ('preceptor', 'analyst', 'service', 'authoring'));

COMMENT ON COLUMN ledger_read_audit.actor_subject_id IS
  'Nullable, no default. NULL = pre-E3 legacy row, actor unknown. '
  'Required for E3+ catalog query kinds (enforced in application layer).';

COMMIT;
