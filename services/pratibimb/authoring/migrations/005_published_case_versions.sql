-- Phase D: published case versions (append-only publish rows; retire is a tombstone patch).
CREATE TABLE IF NOT EXISTS authoring.published_case_versions (
    id VARCHAR NOT NULL PRIMARY KEY,
    draft_id VARCHAR NOT NULL REFERENCES authoring.case_drafts (id),
    tenant_id VARCHAR NOT NULL,
    case_id VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    assessment_mode VARCHAR NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    harness_version VARCHAR NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    published_by VARCHAR NOT NULL,
    retired_at TIMESTAMPTZ,
    retired_by VARCHAR,
    retired_reason_code VARCHAR,
    retired_reason_text TEXT,
    registry_snapshot_json JSONB,
    validation_context_hash VARCHAR(64),
    fixture_hashes JSONB,
    clinical_reviewer VARCHAR,
    -- INVARIANT: Version strings burn permanently. Retired rows still occupy the (tenant_id, case_id, version) slot.
    -- DO NOT add `WHERE retired_at IS NULL` — republish-after-retire is intentionally impossible. See docs/design/authoring_harness.md §2.
    CONSTRAINT uq_published_case_version UNIQUE (tenant_id, case_id, version)
);

CREATE INDEX IF NOT EXISTS ix_published_case_versions_case_id
    ON authoring.published_case_versions (case_id);

CREATE INDEX IF NOT EXISTS ix_published_case_versions_tenant_case_version
    ON authoring.published_case_versions (tenant_id, case_id, version);

