-- 016_regrade_artifact.sql
-- H.a: ncvet_regrader + transcript projection + regrade_audit + regrade_artifacts.
--
-- Apply BEFORE deploying /v1/regrade/ routes that emit caller_kind='ncvet_regrader'
-- or write regrade_audit / regrade_artifacts. Same apply-before-deploy as 012/014/015.

BEGIN;

ALTER TABLE ledger_read_audit
  DROP CONSTRAINT IF EXISTS ck_audit_caller_kind;

ALTER TABLE ledger_read_audit
  ADD CONSTRAINT ck_audit_caller_kind
  CHECK (caller_kind IN (
    'preceptor', 'analyst', 'service', 'authoring',
    'ncvet_audit', 'ncvet_issuer', 'ncvet_verifier', 'ncvet_regrader'
  ));

-- Append-only sealed transcript SoT (regrade path has zero write grant here).
CREATE TABLE IF NOT EXISTS session_transcript_ledger (
    session_id              TEXT        NOT NULL,
    tenant_id               TEXT        NOT NULL,
    transcript_digest       TEXT        NOT NULL,
    rubric_schema_version   TEXT        NOT NULL,
    transcript_bytes        TEXT        NOT NULL,
    sealed_at               TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_id, transcript_digest)
);

CREATE INDEX IF NOT EXISTS ix_session_transcript_ledger_tenant
    ON session_transcript_ledger (tenant_id, sealed_at DESC);

-- F-shaped projection read surface for regrade (I-H-9).
CREATE TABLE IF NOT EXISTS runtime.session_transcript_projection (
    session_id              TEXT        PRIMARY KEY,
    tenant_id               TEXT        NOT NULL,
    transcript_digest       TEXT        NOT NULL,
    rubric_schema_version   TEXT        NOT NULL,
    payload_json            TEXT        NOT NULL,
    projected_at            TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_transcript_proj_tenant
    ON runtime.session_transcript_projection (tenant_id);

-- One row per regrade attempt (success and failure).
CREATE TABLE IF NOT EXISTS regrade_audit (
    regrade_id                      TEXT        PRIMARY KEY,
    tenant_id                       TEXT        NOT NULL,
    session_id                      TEXT        NOT NULL,
    caller_kind                     TEXT        NOT NULL,
    caller_pseudo_id                TEXT        NOT NULL,
    scope                           TEXT        NOT NULL,
    transcript_digest_expected      TEXT        NOT NULL,
    transcript_digest_computed      TEXT        NOT NULL,
    digest_match                    BOOLEAN     NOT NULL,
    grader_invoked                  BOOLEAN     NOT NULL,
    grader_outcome                  TEXT        NOT NULL,
    regrade_artifact_id             TEXT,
    error_kind                      TEXT,
    initiated_at                    TIMESTAMPTZ NOT NULL,
    completed_at                    TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_regrade_audit_caller_kind
      CHECK (caller_kind = 'ncvet_regrader'),
    CONSTRAINT ck_regrade_audit_grader_outcome
      CHECK (grader_outcome IN ('success', 'error', 'not_invoked'))
);

CREATE INDEX IF NOT EXISTS ix_regrade_audit_tenant_initiated
    ON regrade_audit (tenant_id, initiated_at DESC);

CREATE INDEX IF NOT EXISTS ix_regrade_audit_session
    ON regrade_audit (session_id, initiated_at DESC);

-- Portable artifact storage (session_id internal join only).
CREATE TABLE IF NOT EXISTS regrade_artifacts (
    regrade_id              TEXT        PRIMARY KEY,
    tenant_id               TEXT        NOT NULL,
    session_id              TEXT        NOT NULL,
    transcript_digest       TEXT        NOT NULL,
    grader_version          TEXT        NOT NULL,
    rubric_schema_version   TEXT        NOT NULL,
    artifact_schema_version TEXT        NOT NULL,
    regrade_issuer_key_id   TEXT        NOT NULL,
    signed_at               TIMESTAMPTZ NOT NULL,
    valid_until             TIMESTAMPTZ NOT NULL,
    envelope_bytes          TEXT        NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_regrade_artifact_valid_until_after_signed
      CHECK (valid_until > signed_at)
);

CREATE INDEX IF NOT EXISTS ix_regrade_artifacts_tenant
    ON regrade_artifacts (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_regrade_artifacts_session
    ON regrade_artifacts (session_id);

COMMIT;
