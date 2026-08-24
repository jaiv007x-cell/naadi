-- Typed institutional units and time-bounded cohort membership.

CREATE TABLE IF NOT EXISTS institutional_unit (
    tenant_id VARCHAR NOT NULL,
    unit_id VARCHAR NOT NULL,
    unit_type VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL,
    effective_from_utc TIMESTAMPTZ,
    effective_to_utc TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, unit_id)
);

CREATE TABLE IF NOT EXISTS cohort_unit_assignment (
    tenant_id VARCHAR NOT NULL,
    cohort_id VARCHAR NOT NULL,
    unit_id VARCHAR NOT NULL,
    valid_from_utc TIMESTAMPTZ,
    valid_to_utc TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, cohort_id, unit_id)
);

CREATE INDEX IF NOT EXISTS ix_cohort_unit_assignment_unit
    ON cohort_unit_assignment (tenant_id, unit_id);

-- Drop legacy flat mapping if present from earlier scaffold.
DROP TABLE IF EXISTS unit_cohort_mapping;
