-- Frozen content_hash snapshot on every state transition (approve stamp).

ALTER TABLE authoring.case_state_transitions
    ADD COLUMN IF NOT EXISTS content_hash_snapshot VARCHAR(64);
