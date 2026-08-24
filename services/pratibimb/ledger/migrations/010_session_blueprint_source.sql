-- 010_session_blueprint_source.sql
-- Phase E2.2.c: provenance of blueprint_content_hash (I-E22-3, I-E22-4, D1).
--
-- Nullable, NO DEFAULT.
--   NULL source = pre-E2.2.c legacy row (provenance unknown) — includes E1-era
--     rows that stamped a hash before this column existed. Do not backfill.
--   'published' = corpus/published-backed session (hash MUST be present)
--   'seed' = seed-fallback session (hash MUST be null)
--
-- Write-once at ledger append; never UPDATE this column (append-only ledger).

ALTER TABLE session_ledger
    ADD COLUMN IF NOT EXISTS blueprint_source VARCHAR(16);

ALTER TABLE session_ledger
    DROP CONSTRAINT IF EXISTS ck_session_ledger_blueprint_source_enum;

ALTER TABLE session_ledger
    ADD CONSTRAINT ck_session_ledger_blueprint_source_enum
    CHECK (
        blueprint_source IS NULL
        OR blueprint_source IN ('published', 'seed')
    );

ALTER TABLE session_ledger
    DROP CONSTRAINT IF EXISTS ck_session_ledger_blueprint_source_hash;

ALTER TABLE session_ledger
    ADD CONSTRAINT ck_session_ledger_blueprint_source_hash
    CHECK (
        (
            blueprint_source IS NULL
            OR blueprint_source <> 'published'
            OR (
                blueprint_content_hash IS NOT NULL
                AND length(blueprint_content_hash) = 64
            )
        )
        AND (
            blueprint_source IS NULL
            OR blueprint_source <> 'seed'
            OR blueprint_content_hash IS NULL
        )
    );

COMMENT ON COLUMN session_ledger.blueprint_source IS
    'E2.2.c: published | seed | NULL. Nullable, no default — NULL means '
    'pre-E2.2.c legacy (unknown provenance). Do not backfill. Write-once at append.';
