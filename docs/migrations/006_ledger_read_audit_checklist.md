# Migration 006 — Ledger Read Audit Checklist

Apply `services/pratibimb/ledger/migrations/006_ledger_read_audit.sql` before
enabling `AUDIT_SINK=postgres`.

## Pre-flight

- [ ] Backup production ledger DB
- [ ] Verify roles exist: `pratibimb_app`, `pratibimb_auditor` (Postgres only)
- [ ] Confirm `LEDGER_DATABASE_URL` reachable from app and audit pool

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/006_ledger_read_audit.sql
```

## Post-flight verification

- [ ] Table `ledger_read_audit` exists with 15 columns (match `LedgerReadAuditRow`)
- [ ] Partial check constraints: `ck_audit_outcome`, `ck_audit_caller_kind`, `ck_audit_row_count_iff_ok`
- [ ] Indexes: `ix_audit_tenant_at`, `ix_audit_tenant_subj_at`, plus single-column indexes
- [ ] Triggers `trg_audit_no_update`, `trg_audit_no_delete` reject UPDATE/DELETE
- [ ] `pratibimb_app` has INSERT+SELECT; `pratibimb_auditor` has SELECT only
- [ ] Test INSERT as app role succeeds; UPDATE/DELETE raises `insufficient_privilege`

## Application config

- [ ] Set `AUDIT_SINK=postgres`
- [ ] Optional: `LEDGER_AUDIT_POOL_SIZE=5` (separate pool via `get_audit_engine`)
- [ ] Confirm `AuditingLedgerReadService` wired via `build_audited_ledger_read_service`

## Smoke test

- [ ] Successful learner evidence read → one row with `outcome='ok'`, `result_row_count` set
- [ ] Consent denial → one row with `outcome='scope_denied'`
- [ ] k-anonymity block → one row with `outcome='insufficient_cohort'`
- [ ] `query_params_hash` is 64-char hex; no raw learner IDs in hash column

## Rollback

Migration is additive. Rollback = stop writing (`AUDIT_SINK=memory`) and retain
table for forensic retention. Do not DROP in production without legal review.
