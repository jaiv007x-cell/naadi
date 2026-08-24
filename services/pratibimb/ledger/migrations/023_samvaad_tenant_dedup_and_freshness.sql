-- 023_samvaad_tenant_dedup_and_freshness.sql
-- SAMVAAD.e: tenant-scoped dedup and deterministic freshness provenance.
--
-- Apply AFTER migration 022. Existing rows require the operator-reviewed
-- samvaad_023_backfill_manifest before this transaction may complete.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS samvaad_023_backfill_manifest (
    evidence_table          TEXT NOT NULL
        CHECK (evidence_table IN ('summative', 'formative')),
    evidence_id             TEXT NOT NULL,
    tenant_id               TEXT NOT NULL,
    learner_pseudo_id       TEXT NOT NULL,
    session_anchor          TEXT NOT NULL,
    competency_hits_json    TEXT NOT NULL,
    PRIMARY KEY (evidence_table, evidence_id)
);

ALTER TABLE samvaad_summative_evidence
    ADD COLUMN IF NOT EXISTS learner_pseudo_id TEXT,
    ADD COLUMN IF NOT EXISTS session_anchor TEXT;

ALTER TABLE samvaad_formative_evidence
    ADD COLUMN IF NOT EXISTS tenant_id TEXT,
    ADD COLUMN IF NOT EXISTS learner_pseudo_id TEXT,
    ADD COLUMN IF NOT EXISTS competency_hits_json TEXT,
    ADD COLUMN IF NOT EXISTS evidence_digest TEXT;

UPDATE samvaad_summative_evidence AS evidence
SET learner_pseudo_id = manifest.learner_pseudo_id,
    session_anchor = manifest.session_anchor,
    competency_hits_json = manifest.competency_hits_json
FROM samvaad_023_backfill_manifest AS manifest
WHERE manifest.evidence_table = 'summative'
  AND manifest.evidence_id = evidence.evidence_id
  AND (
      evidence.learner_pseudo_id IS NULL
      OR evidence.session_anchor IS NULL
  );

UPDATE samvaad_formative_evidence AS evidence
SET tenant_id = manifest.tenant_id,
    learner_pseudo_id = manifest.learner_pseudo_id,
    session_anchor = manifest.session_anchor,
    competency_hits_json = manifest.competency_hits_json
FROM samvaad_023_backfill_manifest AS manifest
WHERE manifest.evidence_table = 'formative'
  AND manifest.evidence_id = evidence.evidence_id
  AND (
      evidence.tenant_id IS NULL
      OR evidence.learner_pseudo_id IS NULL
      OR evidence.competency_hits_json IS NULL
  );

UPDATE samvaad_formative_evidence
SET evidence_digest = encode(
    digest(
        convert_to(
            jsonb_build_object(
                'learner_pseudo_id', learner_pseudo_id,
                'evidence_class', evidence_class,
                'source_context', source_context_json::jsonb,
                'submitted_by', submitted_by,
                'session_anchor', session_anchor,
                'matcher_parameters', matcher_parameters_json::jsonb,
                'competency_hits', competency_hits_json::jsonb
            )::text,
            'UTF8'
        ),
        'sha256'
    ),
    'hex'
)
WHERE evidence_digest IS NULL;

DO $$
DECLARE
    missing_count BIGINT;
BEGIN
    SELECT count(*) INTO missing_count
    FROM samvaad_summative_evidence
    WHERE learner_pseudo_id IS NULL OR session_anchor IS NULL;
    IF missing_count <> 0 THEN
        RAISE EXCEPTION
            '023 halted: % summative rows missing reviewed learner/session provenance',
            missing_count;
    END IF;

    SELECT count(*) INTO missing_count
    FROM samvaad_formative_evidence
    WHERE tenant_id IS NULL
       OR learner_pseudo_id IS NULL
       OR competency_hits_json IS NULL
       OR evidence_digest IS NULL;
    IF missing_count <> 0 THEN
        RAISE EXCEPTION
            '023 halted: % formative rows missing reviewed tenant/provenance/digest',
            missing_count;
    END IF;
END
$$;

ALTER TABLE samvaad_summative_evidence
    ALTER COLUMN learner_pseudo_id SET NOT NULL,
    ALTER COLUMN session_anchor SET NOT NULL;

ALTER TABLE samvaad_formative_evidence
    ALTER COLUMN tenant_id SET NOT NULL,
    ALTER COLUMN learner_pseudo_id SET NOT NULL,
    ALTER COLUMN competency_hits_json SET NOT NULL,
    ALTER COLUMN evidence_digest SET NOT NULL;

ALTER TABLE samvaad_summative_evidence
    ADD CONSTRAINT uq_samvaad_summative_tenant_digest
        UNIQUE (tenant_id, transcript_digest);

ALTER TABLE samvaad_formative_evidence
    ADD CONSTRAINT uq_samvaad_formative_tenant_digest
        UNIQUE (tenant_id, evidence_digest);

CREATE INDEX IF NOT EXISTS ix_samvaad_formative_tenant_captured
    ON samvaad_formative_evidence (tenant_id, captured_at_utc ASC, evidence_id ASC);

DROP TABLE samvaad_023_backfill_manifest;

COMMIT;
