-- Ledger schema for BEEMA read contract (session_ledger + trusted_physio_version).
-- Matches services/pratibimb/ledger/models.py.

CREATE TABLE IF NOT EXISTS session_ledger (
    session_id VARCHAR NOT NULL PRIMARY KEY,
    learner_pseudo_id VARCHAR NOT NULL,
    cohort_id VARCHAR NOT NULL,
    case_id VARCHAR NOT NULL,
    case_version VARCHAR NOT NULL,
    physio_engine_version VARCHAR NOT NULL,
    rubric_version VARCHAR NOT NULL,
    replay_hash VARCHAR NOT NULL,
    finalized_at_utc TIMESTAMPTZ NOT NULL,
    confirmation VARCHAR NOT NULL DEFAULT 'unconfirmed',
    preceptor_pseudo_id VARCHAR,
    grade_total DOUBLE PRECISION NOT NULL,
    grade_passed BOOLEAN NOT NULL,
    axis_normalized JSON NOT NULL,
    evidence JSON NOT NULL,
    flags JSON NOT NULL,
    actions JSON NOT NULL,
    outcome_link_token VARCHAR
);

CREATE INDEX IF NOT EXISTS ix_session_ledger_learner_pseudo_id
    ON session_ledger (learner_pseudo_id);
CREATE INDEX IF NOT EXISTS ix_session_ledger_cohort_id
    ON session_ledger (cohort_id);
CREATE INDEX IF NOT EXISTS ix_session_ledger_case_id
    ON session_ledger (case_id);
CREATE INDEX IF NOT EXISTS ix_session_ledger_physio_engine_version
    ON session_ledger (physio_engine_version);
CREATE INDEX IF NOT EXISTS ix_session_ledger_finalized_at_utc
    ON session_ledger (finalized_at_utc);

CREATE TABLE IF NOT EXISTS trusted_physio_version (
    version VARCHAR NOT NULL PRIMARY KEY,
    approved_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by VARCHAR NOT NULL,
    notes VARCHAR
);
