# Migration 013 — NCVET Learner Sessions Keyset Index (F.b.1) Checklist

Apply after migration 012 when deploying F.b list pagination.

## Index review (F.b.1)

Migration 012 created:
`ix_session_evidence_projection_tenant_learner (tenant_id, learner_pseudo_id, finalized_at_utc DESC)`

F.b keyset query needs tie-breaker on `session_id`. Migration 013 adds:
`ix_session_evidence_projection_learner_keyset (tenant_id, learner_pseudo_id, finalized_at_utc DESC, session_id DESC)`

## Deploy order

1. Migration 012 (F.a) — required first
2. Migration 013 (F.b) — before or with F.b.1 deploy
3. Deploy F.b.1+ application code

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/013_ncvet_learner_sessions_keyset_index.sql
```

## Post-flight

- [ ] Index `ix_session_evidence_projection_learner_keyset` exists on `runtime.session_evidence_projection`
- [ ] Optional: `EXPLAIN` list query uses index scan on Postgres staging

See also: [`012_ncvet_session_evidence_projection_checklist.md`](012_ncvet_session_evidence_projection_checklist.md)
