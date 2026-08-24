-- Authoring schema: case drafts, state transitions, golden fixtures.
-- Matches services/pratibimb/authoring/models.py.
--
-- Migration lineage is tracked in authoring.alembic_version — separate from
-- any ledger/public version table. When adopting Alembic, set include_schemas=True
-- and scope version_table to this schema only.

CREATE SCHEMA IF NOT EXISTS authoring;

CREATE TABLE IF NOT EXISTS authoring.alembic_version (
    version_num VARCHAR(32) NOT NULL PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS authoring.case_drafts (
    id VARCHAR NOT NULL PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    author_subject_id VARCHAR NOT NULL,
    blueprint_json JSONB NOT NULL,
    blueprint_version VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    current_state VARCHAR NOT NULL,
    harness_version VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_case_drafts_tenant
    ON authoring.case_drafts (tenant_id);

CREATE TABLE IF NOT EXISTS authoring.case_state_transitions (
    id VARCHAR NOT NULL PRIMARY KEY,
    draft_id VARCHAR NOT NULL REFERENCES authoring.case_drafts (id),
    from_state VARCHAR NOT NULL,
    to_state VARCHAR NOT NULL,
    actor_subject_id VARCHAR NOT NULL,
    actor_scope VARCHAR NOT NULL,
    reason TEXT,
    dry_run_result_hash VARCHAR(64),
    occurred_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_dry_run_hash_draft_to_review CHECK (
        (from_state = 'DRAFT' AND to_state = 'IN_REVIEW' AND dry_run_result_hash IS NOT NULL)
        OR NOT (from_state = 'DRAFT' AND to_state = 'IN_REVIEW')
    )
);

CREATE INDEX IF NOT EXISTS ix_case_state_transitions_draft
    ON authoring.case_state_transitions (draft_id);

CREATE TABLE IF NOT EXISTS authoring.golden_fixtures (
    id VARCHAR NOT NULL PRIMARY KEY,
    draft_id VARCHAR NOT NULL REFERENCES authoring.case_drafts (id),
    fixture_kind VARCHAR NOT NULL,
    trace_json JSONB NOT NULL,
    expected_grade_json JSONB NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    CONSTRAINT ck_fixture_kind CHECK (fixture_kind IN ('perfect_path', 'critical_miss')),
    CONSTRAINT uq_golden_fixture_kind UNIQUE (draft_id, fixture_kind)
);
