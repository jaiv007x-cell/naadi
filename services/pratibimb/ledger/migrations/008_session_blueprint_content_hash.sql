-- 008_session_blueprint_content_hash.sql
--
-- Phase E1: stamp published-case blueprint bytes on graded sessions.
-- Distinct from replay_hash (trace integrity).
--
-- NULL means "predates E1 stamping" (legacy / seed / pre-Phase-D sessions).
-- Do NOT backfill. Legacy sessions were often graded against draft blueprints,
-- not published_case_versions; inventing a hash would make an auditor unable
-- to distinguish "stamped at create against a real published version" from
-- "backfilled retroactively against a best-guess version." Null is honest.
--
-- CHECK enforces hex SHA-256 width when present (same discipline as authoring
-- content-hash width guards).

BEGIN;

ALTER TABLE session_ledger
    ADD COLUMN IF NOT EXISTS blueprint_content_hash VARCHAR(64);

ALTER TABLE session_ledger
    DROP CONSTRAINT IF EXISTS ck_session_ledger_blueprint_content_hash_len;

ALTER TABLE session_ledger
    ADD CONSTRAINT ck_session_ledger_blueprint_content_hash_len
    CHECK (
        blueprint_content_hash IS NULL
        OR length(blueprint_content_hash) = 64
    );

COMMENT ON COLUMN session_ledger.blueprint_content_hash IS
    'SHA-256 hex of CaseBlueprintV2.compute_content_hash() snapshotted at '
    'session create from published_case_versions.content_hash. '
    'NULL = predates E1 stamping (not backfilled). '
    'Never rewritten after append; mid-session retirement discoverable via join.';

CREATE INDEX IF NOT EXISTS ix_session_ledger_blueprint_content_hash
    ON session_ledger (blueprint_content_hash)
    WHERE blueprint_content_hash IS NOT NULL;

COMMIT;
