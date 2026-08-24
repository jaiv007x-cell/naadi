# Migration 017 — Regrade Artifact UNIQUE (H.b) Checklist

Apply `services/pratibimb/ledger/migrations/017_regrade_artifact_unique.sql`
**before** deploying H.b.1 code that relies on UNIQUE for `regrade_duplicate`.

## Constraint shape (pinned)

```text
UNIQUE (tenant_id, session_id, transcript_digest)
```

**`grader_version` is deliberately excluded** from the unique key.

| Implication | Pin |
|-------------|-----|
| Intra-tenant | Same session + same sealed transcript → at most one artifact |
| Cross-tenant | Identical digests in different tenants do **not** collide |
| Grader bump | A deploy that changes `GRADER_VERSION` still **409 `regrade_duplicate`** against an existing row — one artifact per `(tenant, session, transcript)` regardless of grader version. Re-issue under a new grader requires an **explicit** future path (not silent overwrite; not automatic unique widening). |

Do not add `grader_version` to this UNIQUE without a design review that opens that explicit re-issue path.

## Deploy order (required)

1. Confirm migration **016** applied (`regrade_artifacts` exists).
2. Apply migration **017** on production ledger DB.
3. Verify unique index `ux_regrade_artifacts_tenant_session_digest` exists.
4. Deploy H.b.1 application build.

Do not reverse this order.

## Rollback order (required — mirror of deploy)

If 017 must be reverted after H.b.1 is live:

1. **First** roll back / redeploy app to a build that does **not** depend on UNIQUE for idempotency (`regrade_duplicate` mapping).
2. **Then** drop the unique index:

```bash
psql "$LEDGER_DATABASE_URL" -c \
  "DROP INDEX IF EXISTS ux_regrade_artifacts_tenant_session_digest;"
```

Do **not** drop the constraint while H.b.1+ code still assumes uniqueness — concurrent inserts can duplicate artifacts and break the idempotency contract the app expects.

Prefer forward-fix (keep UNIQUE; fix app) over rollback when possible.

## Pre-flight

- [ ] Backup production ledger DB
- [ ] No duplicate `(tenant_id, session_id, transcript_digest)` rows already present
  (if duplicates exist, resolve before 017)

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/017_regrade_artifact_unique.sql
```

## Post-flight

- [ ] `\d regrade_artifacts` shows unique index on `(tenant_id, session_id, transcript_digest)` only — **not** including `grader_version`
- [ ] Second INSERT same triple fails at DB
- [ ] App maps failure → `error_kind=regrade_duplicate` + audit row (`grader_invoked=true`)

## Immutability (ops note)

`regrade_audit` / `regrade_artifacts`: INSERT-only from regrade path; no UPDATE grant.
Grep belt in `test_authoring_h_b.py` asserts no UPDATE in regrade package.

## Related

- Design: [`docs/design/authoring_harness_phase_h.md`](../design/authoring_harness_phase_h.md) §H.b C1/C1b
- Prior: [`016_regrade_artifact_checklist.md`](016_regrade_artifact_checklist.md)
