-- 013_ncvet_learner_sessions_keyset_index.sql
-- F.b.1: composite keyset index for list_learner_sessions pagination.
-- Migration 012 index (tenant, learner, finalized_at DESC) lacks session_id tie-breaker.

BEGIN;

CREATE INDEX IF NOT EXISTS ix_session_evidence_projection_learner_keyset
    ON runtime.session_evidence_projection (
        tenant_id,
        learner_pseudo_id,
        finalized_at_utc DESC,
        session_id DESC
    );

COMMIT;
