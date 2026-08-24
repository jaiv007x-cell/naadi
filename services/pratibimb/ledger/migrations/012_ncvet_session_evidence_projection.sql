-- 012_ncvet_session_evidence_projection.sql
-- F.a: ncvet_audit caller_kind + runtime session evidence projection (redacted read surface).

BEGIN;

ALTER TABLE ledger_read_audit
  DROP CONSTRAINT IF EXISTS ck_audit_caller_kind;

ALTER TABLE ledger_read_audit
  ADD CONSTRAINT ck_audit_caller_kind
  CHECK (caller_kind IN (
    'preceptor', 'analyst', 'service', 'authoring', 'ncvet_audit'
  ));

CREATE TABLE IF NOT EXISTS runtime.session_evidence_projection (
    session_id              TEXT        PRIMARY KEY,
    tenant_id               TEXT        NOT NULL,
    learner_pseudo_id       TEXT        NOT NULL,
    cohort_id               TEXT        NOT NULL,
    case_id                 TEXT        NOT NULL,
    case_version            TEXT        NOT NULL,
    finalized_at_utc        TIMESTAMPTZ NOT NULL,
    physio_engine_version   TEXT        NOT NULL,
    rubric_version          TEXT        NOT NULL,
    replay_hash             TEXT        NOT NULL,
    blueprint_content_hash  TEXT,
    blueprint_source        TEXT,
    grade_total             DOUBLE PRECISION NOT NULL,
    grade_passed            BOOLEAN     NOT NULL,
    axis_normalized         JSONB       NOT NULL,
    evidence                JSONB       NOT NULL,
    flags                   JSONB       NOT NULL,
    actions                 JSONB       NOT NULL,
    case_context_json       TEXT        NOT NULL,
    projected_at            TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_session_evidence_projection_tenant_session
    ON runtime.session_evidence_projection (tenant_id, session_id);

CREATE INDEX IF NOT EXISTS ix_session_evidence_projection_tenant_learner
    ON runtime.session_evidence_projection (tenant_id, learner_pseudo_id, finalized_at_utc DESC);

COMMENT ON TABLE runtime.session_evidence_projection IS
  'F.a: NCVET read projection — redacted allowlist fields only. '
  'Rebuildable from session_ledger; ncvet reads MUST NOT query session_ledger directly.';

COMMIT;
