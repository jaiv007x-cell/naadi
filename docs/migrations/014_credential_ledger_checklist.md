# Migration 014 — Credential Ledger (G.a) Checklist

Apply `services/pratibimb/ledger/migrations/014_credential_ledger.sql`
**before** deploying code that writes `caller_kind='ncvet_issuer'` or
touches `credential_ledger` / `/v1/credentials/`.

If the app deploys first, issuer audit inserts fail at `ck_audit_caller_kind`;
issue writes fail with missing table.

## Deploy order (required)

1. Confirm migrations **012** and **013** already applied (F surface).
2. Apply migration **014** on production ledger DB.
3. Verify `ck_audit_caller_kind` includes **`ncvet_issuer`** (and still `ncvet_audit`).
4. Verify table **`credential_ledger`** exists with CHECKs and indexes.
5. Deploy application build containing G.a credentials routes.

Do not reverse this order.

## Closed caller_kind set (app + DB must match)

Validated at import in `audit/caller_kinds.py` and at DB via `ck_audit_caller_kind`:

```text
preceptor, analyst, service, authoring, ncvet_audit, ncvet_issuer
```

**Forward-ref (G.b):** `ncvet_verifier` is **not** in this migration — add via a later migration when verify/status-list lands. Do not treat absence as an accidental omission.

## Pre-flight

- [ ] Backup production ledger DB
- [ ] Confirm `LEDGER_DATABASE_URL` reachable
- [ ] Migrations 006, 011, 012, 013 applied
- [ ] App `KNOWN_AUDIT_CALLER_KINDS` about to deploy includes `ncvet_issuer`

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/014_credential_ledger.sql
```

## Post-flight verification

- [ ] `ck_audit_caller_kind` allows `preceptor`, `analyst`, `service`, `authoring`, `ncvet_audit`, **`ncvet_issuer`**
- [ ] Table `credential_ledger` exists with PK `(credential_id, credential_version)`
- [ ] Unique index on `evidence_ref`
- [ ] CHECK `ck_credential_evidence_ref_revoked_after_issued`:
      `evidence_ref_revoked_at IS NULL OR evidence_ref_revoked_at >= issued_at`
- [ ] CHECK `ck_credential_revoked_after_issued`:
      `revoked_at IS NULL OR revoked_at >= issued_at`
- [ ] Test INSERT into `ledger_read_audit` with `caller_kind='ncvet_issuer'` and non-null `actor_subject_id` succeeds
- [ ] Test INSERT into `credential_ledger` with valid timestamps succeeds; `evidence_ref_revoked_at < issued_at` fails

## Rollback note

Removing `ncvet_issuer` from the CHECK or dropping `credential_ledger` after G.a deploy requires coordinated code rollback. Prefer forward-fix: leave objects in place even if credential routes are feature-flagged off.

## Related

- Design: [`docs/design/authoring_harness_phase_g.md`](../design/authoring_harness_phase_g.md) §G.a detail
- F prereqs: [`012_ncvet_session_evidence_projection_checklist.md`](012_ncvet_session_evidence_projection_checklist.md), [`013_ncvet_learner_sessions_keyset_index_checklist.md`](013_ncvet_learner_sessions_keyset_index_checklist.md)
