-- Institutional unit → ledger cohort mapping.
-- unit_id = training slice (ward rotation, intake batch, NCVET audit cohort).

CREATE TABLE IF NOT EXISTS unit_cohort_mapping (
    unit_id VARCHAR NOT NULL,
    cohort_id VARCHAR NOT NULL,
    role VARCHAR,
    label VARCHAR,
    active_from_utc TIMESTAMPTZ,
    active_until_utc TIMESTAMPTZ,
    PRIMARY KEY (unit_id, cohort_id)
);

CREATE INDEX IF NOT EXISTS ix_unit_cohort_mapping_unit_id
    ON unit_cohort_mapping (unit_id);
