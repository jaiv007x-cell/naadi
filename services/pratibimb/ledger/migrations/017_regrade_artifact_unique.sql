-- 017_regrade_artifact_unique.sql
-- H.b: UNIQUE (tenant_id, session_id, transcript_digest) on regrade_artifacts.
--
-- grader_version is deliberately NOT in the unique key: one artifact per sealed
-- transcript identity; a GRADER_VERSION bump still collides (409 regrade_duplicate)
-- until an explicit re-issue path is designed.
--
-- Apply BEFORE deploying H.b.1 code that maps IntegrityError → regrade_duplicate.
-- Same apply-in-prod-before-deploy discipline as 012/014/015/016.
-- Rollback: app first, then DROP INDEX (see checklist).

BEGIN;

-- One authoritative artifact per sealed transcript identity within a tenant.
CREATE UNIQUE INDEX IF NOT EXISTS ux_regrade_artifacts_tenant_session_digest
    ON regrade_artifacts (tenant_id, session_id, transcript_digest);

COMMIT;
