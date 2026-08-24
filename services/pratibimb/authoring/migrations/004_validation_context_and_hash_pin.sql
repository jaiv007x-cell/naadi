-- Submit-time registry pin + approval validation context hash.
-- Compile at APPROVED uses registry_snapshot_json from DRAFT→IN_REVIEW,
-- not live catalogs. validation_context_hash is the durable "approved against X".

ALTER TABLE authoring.case_state_transitions
    ADD COLUMN IF NOT EXISTS validation_context_hash VARCHAR(64);

-- Inline JSON snapshot of registry state at submit. Fine at current scale; if rows
-- routinely exceed a few KB (large rubrics, many action IDs / deprecations), move
-- the blob to content-addressed storage and store a hash-pointer on the row instead.
ALTER TABLE authoring.case_state_transitions
    ADD COLUMN IF NOT EXISTS registry_snapshot_json JSONB;

ALTER TABLE authoring.approval_events
    ADD COLUMN IF NOT EXISTS validation_context_hash VARCHAR(64);

-- Belt: APPROVED snapshot must equal the latest submit freeze (Postgres).
CREATE OR REPLACE FUNCTION authoring.reject_content_hash_drift()
RETURNS trigger AS $$
DECLARE
    submit_hash VARCHAR(64);
BEGIN
    IF NEW.from_state = 'IN_REVIEW' AND NEW.to_state = 'APPROVED' THEN
        SELECT t.content_hash_snapshot INTO submit_hash
        FROM authoring.case_state_transitions t
        WHERE t.draft_id = NEW.draft_id
          AND t.from_state = 'DRAFT'
          AND t.to_state = 'IN_REVIEW'
        ORDER BY t.occurred_at DESC
        LIMIT 1;
        IF submit_hash IS NOT NULL AND NEW.content_hash_snapshot IS DISTINCT FROM submit_hash THEN
            RAISE EXCEPTION 'content_hash_snapshot drift vs submit freeze';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS transitions_content_hash_matches_submit
    ON authoring.case_state_transitions;
CREATE TRIGGER transitions_content_hash_matches_submit
    BEFORE INSERT ON authoring.case_state_transitions
    FOR EACH ROW EXECUTE PROCEDURE authoring.reject_content_hash_drift();
