-- 019_arp_artifact.sql
-- I.a: ncvet_recompute (+ reserved ncvet_arp_verifier) + arp_audit + arp_artifacts
--      + arp_recompute_eligible_rubrics.
--
-- Apply BEFORE deploying /v1/arp/ routes. 018 is samvaad_verifier — do not reuse.

BEGIN;

ALTER TABLE ledger_read_audit
  DROP CONSTRAINT IF EXISTS ck_audit_caller_kind;

ALTER TABLE ledger_read_audit
  ADD CONSTRAINT ck_audit_caller_kind
  CHECK (caller_kind IN (
    'preceptor', 'analyst', 'service', 'authoring',
    'ncvet_audit', 'ncvet_issuer', 'ncvet_verifier', 'ncvet_regrader',
    'samvaad_verifier',
    'ncvet_recompute', 'ncvet_arp_verifier'
  ));

-- Published, recompute-eligible alternate rubrics (eligibility checked at grader invoke).
CREATE TABLE IF NOT EXISTS arp_recompute_eligible_rubrics (
    tenant_id                   TEXT        NOT NULL,
    alternate_rubric_id         TEXT        NOT NULL,
    alternate_rubric_version    TEXT        NOT NULL,
    authority_id                TEXT        NOT NULL,
    published                   BOOLEAN     NOT NULL DEFAULT TRUE,
    recompute_eligible          BOOLEAN     NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant_id, alternate_rubric_id, alternate_rubric_version)
);

CREATE TABLE IF NOT EXISTS arp_audit (
    arp_id                              TEXT        PRIMARY KEY,
    tenant_id                           TEXT        NOT NULL,
    session_id                          TEXT        NOT NULL,
    caller_kind                         TEXT        NOT NULL,
    caller_pseudo_id                    TEXT        NOT NULL,
    scope                               TEXT        NOT NULL,
    transcript_digest_expected          TEXT        NOT NULL,
    transcript_digest_computed          TEXT        NOT NULL,
    digest_match                        BOOLEAN     NOT NULL,
    alternate_rubric_id                 TEXT        NOT NULL,
    alternate_rubric_version            TEXT,
    authority_id                        TEXT,
    transcript_rubric_schema_version    TEXT        NOT NULL,
    sealed_rubric_id                    TEXT        NOT NULL,
    grader_invoked                      BOOLEAN     NOT NULL,
    grader_outcome                      TEXT        NOT NULL,
    arp_artifact_id                     TEXT,
    error_kind                          TEXT,
    initiated_at                        TIMESTAMPTZ NOT NULL,
    completed_at                        TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_arp_audit_caller_kind
      CHECK (caller_kind = 'ncvet_recompute'),
    CONSTRAINT ck_arp_audit_grader_outcome
      CHECK (grader_outcome IN ('success', 'error', 'not_invoked'))
);

CREATE INDEX IF NOT EXISTS ix_arp_audit_tenant_initiated
    ON arp_audit (tenant_id, initiated_at DESC);

CREATE INDEX IF NOT EXISTS ix_arp_audit_session
    ON arp_audit (session_id, initiated_at DESC);

CREATE TABLE IF NOT EXISTS arp_artifacts (
    arp_id                      TEXT        PRIMARY KEY,
    tenant_id                   TEXT        NOT NULL,
    session_id                  TEXT        NOT NULL,
    transcript_digest           TEXT        NOT NULL,
    alternate_rubric_id         TEXT        NOT NULL,
    alternate_rubric_version    TEXT        NOT NULL,
    authority_id                TEXT        NOT NULL,
    sealed_rubric_id            TEXT        NOT NULL,
    transcript_rubric_schema_version TEXT   NOT NULL,
    grader_version              TEXT        NOT NULL,
    artifact_schema_version     TEXT        NOT NULL,
    arp_issuer_key_id           TEXT        NOT NULL,
    signed_at                   TIMESTAMPTZ NOT NULL,
    valid_until                 TIMESTAMPTZ NOT NULL,
    envelope_bytes              TEXT        NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_arp_artifact_valid_until_after_signed
      CHECK (valid_until > signed_at),
    CONSTRAINT ux_arp_artifacts_tenant_session_digest_rubric
      UNIQUE (tenant_id, session_id, transcript_digest, alternate_rubric_id)
);

CREATE INDEX IF NOT EXISTS ix_arp_artifacts_tenant
    ON arp_artifacts (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_arp_artifacts_session
    ON arp_artifacts (session_id);

COMMIT;
