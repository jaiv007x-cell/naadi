-- 009_runtime_published_case_corpus.sql
-- Phase E2.1: rebuildable projection of published/retired cases for sampler.
-- PK = authoring.published_case_versions.id (shared identity, no mapping table).
-- envelope_json is TEXT holding canonical JSON (sort_keys, compact separators)
-- so re-projection is byte-idempotent; not JSONB to avoid re-serialize drift.
-- probe_only is ALWAYS derived from published.retired_at — never a write-time arg.

CREATE SCHEMA IF NOT EXISTS runtime;

CREATE TABLE IF NOT EXISTS runtime.published_case_corpus (
    id VARCHAR NOT NULL PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    case_id VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    assessment_mode VARCHAR NOT NULL,
    workflow_state VARCHAR NOT NULL,
    probe_only BOOLEAN NOT NULL,
    harness_version VARCHAR NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    envelope_json TEXT NOT NULL,
    projected_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_runtime_published_case_corpus_slot
        UNIQUE (tenant_id, case_id, version),
    CONSTRAINT ck_runtime_corpus_content_hash_len
        CHECK (length(content_hash) = 64),
    CONSTRAINT ck_runtime_corpus_workflow_state
        CHECK (workflow_state IN ('PUBLISHED', 'RETIRED')),
    CONSTRAINT ck_runtime_corpus_probe_matches_state
        CHECK (
            (probe_only IS TRUE AND workflow_state = 'RETIRED')
            OR (probe_only IS FALSE AND workflow_state = 'PUBLISHED')
        )
);

CREATE INDEX IF NOT EXISTS ix_runtime_corpus_tenant_probe
    ON runtime.published_case_corpus (tenant_id, probe_only);

CREATE INDEX IF NOT EXISTS ix_runtime_corpus_tenant_case_version
    ON runtime.published_case_corpus (tenant_id, case_id, version);

COMMENT ON TABLE runtime.published_case_corpus IS
    'E2 projection of authoring.published_case_versions for sampler/runtime. '
    'Rebuildable cache; authoring remains lifecycle SoT.';

COMMENT ON COLUMN runtime.published_case_corpus.id IS
    'Equals authoring.published_case_versions.id — shared PK, no mapping.';

COMMENT ON COLUMN runtime.published_case_corpus.probe_only IS
    'Derived only: true iff source published row has retired_at IS NOT NULL.';

COMMENT ON COLUMN runtime.published_case_corpus.content_hash IS
    'Copied from published_case_versions.content_hash — never recomputed as write source.';

COMMENT ON COLUMN runtime.published_case_corpus.envelope_json IS
    'Canonical JSON text (sorted keys, compact). Re-project of immutable publish must be byte-identical.';
