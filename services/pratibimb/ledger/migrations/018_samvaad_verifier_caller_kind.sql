-- 018_samvaad_verifier_caller_kind.sql
-- SAMVAAD.a: extend ledger_read_audit.caller_kind with samvaad_verifier.
--
-- Apply BEFORE deploying any path that emits caller_kind='samvaad_verifier'.
-- Same apply-before-deploy discipline as 012/014/015/016.

BEGIN;

ALTER TABLE ledger_read_audit
  DROP CONSTRAINT IF EXISTS ck_audit_caller_kind;

ALTER TABLE ledger_read_audit
  ADD CONSTRAINT ck_audit_caller_kind
  CHECK (caller_kind IN (
    'preceptor', 'analyst', 'service', 'authoring',
    'ncvet_audit', 'ncvet_issuer', 'ncvet_verifier', 'ncvet_regrader',
    'samvaad_verifier'
  ));

COMMIT;
