# Migration 011 — Ledger Read Audit Actor (E3.a) Checklist

Apply `services/pratibimb/ledger/migrations/011_ledger_read_audit_actor.sql`
**before** deploying code that writes `actor_subject_id` on catalog audit rows.

Nullable-legacy protects **old rows without a value**, not **a missing column**.
If the app deploys first, E3+ catalog inserts will fail at the DB layer.

## Deploy order (required)

1. Apply migration 011 on production ledger DB.
2. Verify `actor_subject_id` column exists and `ck_audit_caller_kind` includes `authoring`.
3. Deploy application build containing E3.a+ catalog read paths.

Do not reverse this order.

## Pre-flight

- [ ] Backup production ledger DB
- [ ] Confirm `LEDGER_DATABASE_URL` reachable
- [ ] Migration 006 already applied (`ledger_read_audit` table exists)

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/011_ledger_read_audit_actor.sql
```

## Post-flight verification

- [ ] Column `actor_subject_id` exists on `ledger_read_audit` (nullable TEXT)
- [ ] `ck_audit_caller_kind` allows `preceptor`, `analyst`, `service`, **and** `authoring`
- [ ] Pre-E3 rows remain valid with `actor_subject_id IS NULL` (no backfill)
- [ ] Test INSERT with `caller_kind='authoring'` and non-null `actor_subject_id` succeeds

## Rollback note

Dropping `actor_subject_id` after E3.a deploy requires a coordinated code rollback.
Prefer forward-fix: leave column in place even if catalog routes are disabled.
