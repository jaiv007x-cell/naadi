-- 022_samvaad_formative_evidence.sql
-- SAMVAAD.d: formative evidence capture (INSERT-only; patient_reported permitted)
--
-- Apply AFTER migration 021. Sequential owner: SAMVAAD.d.

BEGIN;

-- Tighten already-deployed 021 databases to the summative-authoritative shape.
ALTER TABLE samvaad_summative_evidence
    ADD COLUMN IF NOT EXISTS assessment_kind TEXT NOT NULL DEFAULT 'summative';
ALTER TABLE samvaad_summative_evidence
    DROP CONSTRAINT IF EXISTS ck_samvaad_evidence_class;
ALTER TABLE samvaad_summative_evidence
    DROP CONSTRAINT IF EXISTS ck_samvaad_summative_evidence_class;
ALTER TABLE samvaad_summative_evidence
    DROP CONSTRAINT IF EXISTS ck_samvaad_summative_assessment_kind;
ALTER TABLE samvaad_summative_evidence
    ADD CONSTRAINT ck_samvaad_summative_assessment_kind
        CHECK (assessment_kind = 'summative'),
    ADD CONSTRAINT ck_samvaad_summative_evidence_class
        CHECK (evidence_class IN ('machine_sim', 'preceptor_attested'));

CREATE TABLE IF NOT EXISTS samvaad_formative_evidence (
    evidence_id                 TEXT        PRIMARY KEY,
    assessment_kind             TEXT        NOT NULL DEFAULT 'formative',
    evidence_class              TEXT        NOT NULL,
    source_context_json         TEXT        NOT NULL,
    submitted_by                TEXT        NOT NULL,
    session_anchor              TEXT        NOT NULL,
    matcher_parameters_json     TEXT        NOT NULL,
    captured_at_utc             TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_samvaad_formative_assessment_kind
        CHECK (assessment_kind = 'formative'),
    CONSTRAINT ck_samvaad_formative_evidence_class
        CHECK (evidence_class IN ('machine_sim', 'preceptor_attested', 'patient_reported'))
);

CREATE INDEX IF NOT EXISTS ix_samvaad_formative_captured
    ON samvaad_formative_evidence (captured_at_utc DESC);

COMMIT;
