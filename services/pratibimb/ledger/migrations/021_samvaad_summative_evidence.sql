-- 021_samvaad_summative_evidence.sql
-- SAMVAAD.c: summative evidence SoT table (INSERT-only)
--
-- Apply AFTER migrations 018–020. Sequential owner: SAMVAAD.c.

BEGIN;

CREATE TABLE IF NOT EXISTS samvaad_summative_evidence (
    evidence_id                 TEXT        PRIMARY KEY,
    tenant_id                   TEXT        NOT NULL,
    transcript_digest           TEXT        NOT NULL,
    grader_version              TEXT        NOT NULL,
    rubric_schema_version       TEXT        NOT NULL,
    artifact_schema_version     TEXT        NOT NULL,
    evidence_class              TEXT        NOT NULL,
    framework_citation_anchor   TEXT,
    competency_hits_json        TEXT        NOT NULL DEFAULT '[]',
    captured_at_utc             TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_samvaad_evidence_class
        CHECK (evidence_class IN ('machine_sim', 'preceptor_attested', 'patient_reported'))
);

CREATE INDEX IF NOT EXISTS ix_samvaad_summative_tenant_captured
    ON samvaad_summative_evidence (tenant_id, captured_at_utc DESC);

COMMIT;
