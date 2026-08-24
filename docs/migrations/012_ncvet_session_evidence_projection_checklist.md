# Migration 012 — NCVET Session Evidence Projection (F.a) Checklist

Apply `services/pratibimb/ledger/migrations/012_ncvet_session_evidence_projection.sql`
**before** deploying code that writes `caller_kind='ncvet_audit'` or reads
`runtime.session_evidence_projection`.

If the app deploys first, NCVET audit inserts fail at `ck_audit_caller_kind`;
projection reads fail with missing table.

## Deploy order (required)

1. Apply migration 012 on production ledger DB.
2. Verify `ck_audit_caller_kind` includes **`ncvet_audit`**.
3. Verify table **`runtime.session_evidence_projection`** exists with indexes.
4. Deploy application build containing F.a+ NCVET read paths and `EvidenceProjector` hook.

Do not reverse this order.

## Pre-flight

- [ ] Backup production ledger DB
- [ ] Confirm `LEDGER_DATABASE_URL` reachable
- [ ] Migration 006 already applied (`ledger_read_audit` table exists)
- [ ] Runtime schema exists (Postgres `CREATE SCHEMA runtime` — not SQLite ATTACH)

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/012_ncvet_session_evidence_projection.sql
```

## Post-flight verification

- [ ] `ck_audit_caller_kind` allows `preceptor`, `analyst`, `service`, `authoring`, **`ncvet_audit`**
- [ ] Table `runtime.session_evidence_projection` exists (PK `session_id`)
- [ ] Index `ix_session_evidence_projection_tenant_learner` on `(tenant_id, learner_pseudo_id, finalized_at_utc DESC)`
- [ ] **F.b deploy:** also apply migration 013 for keyset index — see [`013_ncvet_learner_sessions_keyset_index_checklist.md`](013_ncvet_learner_sessions_keyset_index_checklist.md)
- [ ] Test INSERT into `ledger_read_audit` with `caller_kind='ncvet_audit'` and non-null `actor_subject_id` succeeds
- [ ] Optional: backfill projection rows for pre-F.a graded sessions before enabling NCVET reads (otherwise legitimate 404 until graded post-deploy)

## Rollback note

Dropping `runtime.session_evidence_projection` or removing `ncvet_audit` from the CHECK after F.a deploy requires coordinated code rollback. Prefer forward-fix: leave objects in place even if NCVET routes are feature-flagged off.

## Related runbook

After F.c: exfil-burst SQL for `list_learner_sessions` reuses E3 retirement-history detector shape (`page_ordinal` / cursor in `query_params_hash`). See ops runbook under `docs/ops/` (F.c extends with NCVET section).
