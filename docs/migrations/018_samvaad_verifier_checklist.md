# Migration 018 — `samvaad_verifier` caller_kind (SAMVAAD.a) Checklist

Apply `services/pratibimb/ledger/migrations/018_samvaad_verifier_caller_kind.sql`
**before** deploying any code that emits `caller_kind='samvaad_verifier'`.

## Constraint shape (pinned)

```text
caller_kind IN (
  'preceptor', 'analyst', 'service', 'authoring',
  'ncvet_audit', 'ncvet_issuer', 'ncvet_verifier', 'ncvet_regrader',
  'samvaad_verifier'
)
```

Sequential after **017**. No parallel `samvaad_evidence_audit` table in this migration.

## Deploy order (required)

1. Confirm migrations **016** and **017** applied.
2. Apply migration **018** on production ledger DB.
3. Verify `ck_audit_caller_kind` includes **`samvaad_verifier`**.
4. Deploy application build with `KNOWN_AUDIT_CALLER_KINDS` including `samvaad_verifier`.

Do not reverse this order. **Same-commit discipline:** app enum + model CHECK + SQL.

## Rollback order

1. Roll back / redeploy app so nothing emits `samvaad_verifier`.
2. Then restore prior CHECK (kinds through `ncvet_regrader` only) via a follow-up migration — do not hand-edit production CHECK without a numbered migration.

## Verify

```bash
psql "$LEDGER_DATABASE_URL" -c \
  "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_audit_caller_kind';"
```

- [ ] `ck_audit_caller_kind` allows prior kinds + **`samvaad_verifier`**
- [ ] App `KNOWN_AUDIT_CALLER_KINDS` includes `samvaad_verifier`
- [ ] `pytest -m samvaad_acceptance` green
