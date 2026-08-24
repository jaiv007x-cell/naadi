-- Phase D: append-only retirement audit rows.
-- Authoritative history lives in case_state_transitions; retired_* on
-- published_case_versions is denormalized query convenience only.

CREATE TABLE IF NOT EXISTS authoring.retired_case_versions (
    id VARCHAR NOT NULL PRIMARY KEY,
    published_case_version_id VARCHAR NOT NULL
        REFERENCES authoring.published_case_versions (id),
    draft_id VARCHAR NOT NULL REFERENCES authoring.case_drafts (id),
    tenant_id VARCHAR NOT NULL,
    case_id VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    retired_at TIMESTAMPTZ NOT NULL,
    retired_by VARCHAR NOT NULL,
    reason_code VARCHAR NOT NULL,
    reason_text TEXT,
    CONSTRAINT ck_retire_reason_code CHECK (
        reason_code IN (
            'clinical_error',
            'superseded_by_new_version',
            'guideline_change',
            'policy_change',
            'operator_request'
        )
    )
);

CREATE INDEX IF NOT EXISTS ix_retired_case_versions_published
    ON authoring.retired_case_versions (published_case_version_id);

CREATE INDEX IF NOT EXISTS ix_retired_case_versions_case
    ON authoring.retired_case_versions (tenant_id, case_id, version);
