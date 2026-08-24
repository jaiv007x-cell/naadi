-- Phase C: append-only approval events + authoring audit trail.
-- Approvals are never UPDATEd or DELETEd; withdraw is a new row.

CREATE TABLE IF NOT EXISTS authoring.approval_events (
    id VARCHAR NOT NULL PRIMARY KEY,
    draft_id VARCHAR NOT NULL REFERENCES authoring.case_drafts (id),
    event_type VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    actor_subject_id VARCHAR NOT NULL,
    actor_scope VARCHAR NOT NULL,
    dry_run_result_hash VARCHAR(64),
    submit_dry_run_hash VARCHAR(64),
    content_hash VARCHAR(64) NOT NULL,
    reason TEXT,
    supersedes_event_id VARCHAR,
    occurred_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_approval_kind CHECK (kind IN ('FORMATIVE', 'SUMMATIVE')),
    CONSTRAINT ck_approval_event_type CHECK (event_type IN ('GRANT', 'WITHDRAW')),
    CONSTRAINT ck_approval_scope_matches_kind CHECK (
        (kind = 'FORMATIVE' AND actor_scope = 'authoring:approve')
        OR (kind = 'SUMMATIVE' AND actor_scope = 'authoring:approve_summative')
    ),
    CONSTRAINT ck_approval_grant_has_hash CHECK (
        (event_type = 'GRANT' AND dry_run_result_hash IS NOT NULL)
        OR (event_type = 'WITHDRAW')
    ),
    CONSTRAINT ck_approval_withdraw_has_target CHECK (
        (event_type = 'WITHDRAW' AND supersedes_event_id IS NOT NULL)
        OR (event_type = 'GRANT')
    )
);

CREATE INDEX IF NOT EXISTS ix_approval_events_draft
    ON authoring.approval_events (draft_id);

CREATE TABLE IF NOT EXISTS authoring.authoring_audit (
    id VARCHAR NOT NULL PRIMARY KEY,
    artifact_type VARCHAR NOT NULL,
    draft_id VARCHAR NOT NULL REFERENCES authoring.case_drafts (id),
    case_id VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    from_state VARCHAR NOT NULL,
    to_state VARCHAR NOT NULL,
    actor_subject_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    at_utc TIMESTAMPTZ NOT NULL,
    content_hash VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_authoring_audit_draft
    ON authoring.authoring_audit (draft_id);

-- Append-only: refuse UPDATE/DELETE at the database.
CREATE OR REPLACE FUNCTION authoring.refuse_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS approval_events_no_update ON authoring.approval_events;
CREATE TRIGGER approval_events_no_update
    BEFORE UPDATE OR DELETE ON authoring.approval_events
    FOR EACH ROW EXECUTE PROCEDURE authoring.refuse_mutation();

DROP TRIGGER IF EXISTS authoring_audit_no_update ON authoring.authoring_audit;
CREATE TRIGGER authoring_audit_no_update
    BEFORE UPDATE OR DELETE ON authoring.authoring_audit
    FOR EACH ROW EXECUTE PROCEDURE authoring.refuse_mutation();

-- Same principal cannot hold two active summative GRANTs.
CREATE OR REPLACE FUNCTION authoring.reject_duplicate_summative_approver()
RETURNS trigger AS $$
BEGIN
    IF NEW.event_type = 'GRANT' AND NEW.kind = 'SUMMATIVE' THEN
        IF EXISTS (
            SELECT 1
            FROM authoring.approval_events g
            WHERE g.draft_id = NEW.draft_id
              AND g.kind = 'SUMMATIVE'
              AND g.event_type = 'GRANT'
              AND g.actor_subject_id = NEW.actor_subject_id
              AND NOT EXISTS (
                  SELECT 1
                  FROM authoring.approval_events w
                  WHERE w.event_type = 'WITHDRAW'
                    AND w.supersedes_event_id = g.id
              )
        ) THEN
            RAISE EXCEPTION 'duplicate summative approver %', NEW.actor_subject_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS approval_events_distinct_summative ON authoring.approval_events;
CREATE TRIGGER approval_events_distinct_summative
    BEFORE INSERT ON authoring.approval_events
    FOR EACH ROW EXECUTE PROCEDURE authoring.reject_duplicate_summative_approver();
