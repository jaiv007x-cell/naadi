-- 014_credential_ledger.sql
-- G.a: ncvet_issuer caller_kind + append-only credential_ledger (signed credentials).
--
-- Apply before deploying /v1/credentials/ issue + evidence_ref fetch paths.
-- Forward-ref: ncvet_verifier lands in G.b (not in this CHECK yet).

BEGIN;

ALTER TABLE ledger_read_audit
  DROP CONSTRAINT IF EXISTS ck_audit_caller_kind;

ALTER TABLE ledger_read_audit
  ADD CONSTRAINT ck_audit_caller_kind
  CHECK (caller_kind IN (
    'preceptor', 'analyst', 'service', 'authoring', 'ncvet_audit', 'ncvet_issuer'
  ));

CREATE TABLE IF NOT EXISTS credential_ledger (
    credential_id           TEXT        NOT NULL,
    credential_version      INTEGER     NOT NULL,
    tenant_id               TEXT        NOT NULL,
    evidence_ref            TEXT        NOT NULL,
    session_id              TEXT        NOT NULL,
    issued_at               TIMESTAMPTZ NOT NULL,
    revoked_at              TIMESTAMPTZ,
    evidence_ref_revoked_at TIMESTAMPTZ,
    projection_digest       TEXT        NOT NULL,
    digest_alg              TEXT        NOT NULL,
    audit_query_id          TEXT        NOT NULL,
    audit_fingerprint       TEXT,
    blueprint_version       TEXT        NOT NULL,
    grading_outcome_version TEXT        NOT NULL,
    issuer_key_id           TEXT        NOT NULL,
    credential_bytes        TEXT        NOT NULL,
    PRIMARY KEY (credential_id, credential_version),
    CONSTRAINT ck_credential_evidence_ref_revoked_after_issued
      CHECK (
        evidence_ref_revoked_at IS NULL
        OR evidence_ref_revoked_at >= issued_at
      ),
    CONSTRAINT ck_credential_revoked_after_issued
      CHECK (
        revoked_at IS NULL
        OR revoked_at >= issued_at
      )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_credential_ledger_evidence_ref
    ON credential_ledger (evidence_ref);

CREATE INDEX IF NOT EXISTS ix_credential_ledger_tenant_issued
    ON credential_ledger (tenant_id, issued_at DESC);

CREATE INDEX IF NOT EXISTS ix_credential_ledger_session
    ON credential_ledger (tenant_id, session_id);

CREATE INDEX IF NOT EXISTS ix_credential_ledger_revoked
    ON credential_ledger (revoked_at)
    WHERE revoked_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_credential_ledger_evidence_ref_revoked
    ON credential_ledger (evidence_ref_revoked_at)
    WHERE evidence_ref_revoked_at IS NOT NULL;

COMMIT;
