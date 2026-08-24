# Migration 015 — Credential Status List (G.b) Checklist

Apply `services/pratibimb/ledger/migrations/015_credential_status_list.sql`
**before** deploying code that writes `caller_kind='ncvet_verifier'` or
serves `/v1/credentials/status_list` / revoke paths.

If the app deploys first, verifier audit inserts fail at `ck_audit_caller_kind`.

## Deploy order (required)

1. Confirm migrations **012**, **013**, **014** applied.
2. Apply migration **015** on production ledger DB.
3. Verify `ck_audit_caller_kind` includes **`ncvet_verifier`**.
4. Verify table **`credential_status_list_snapshot`** exists with CHECKs.
5. Deploy application build containing G.b verify / status / revoke routes.

Do not reverse this order. **Same-commit discipline:** app `KNOWN_AUDIT_CALLER_KINDS`
must gain `ncvet_verifier` in the same release as this migration and the emitters.

## Closed caller_kind set (app + DB must match)

```text
preceptor, analyst, service, authoring, ncvet_audit, ncvet_issuer, ncvet_verifier
```

## Pre-flight

- [ ] Backup production ledger DB
- [ ] Confirm `LEDGER_DATABASE_URL` reachable
- [ ] Migrations 014 applied (`credential_ledger` exists)
- [ ] App about to deploy includes `ncvet_verifier` in `caller_kinds.py`

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/015_credential_status_list.sql
```

## Post-flight verification

- [ ] `ck_audit_caller_kind` allows prior kinds + **`ncvet_verifier`**
- [ ] Table `credential_status_list_snapshot` exists
- [ ] CHECK `valid_until > signed_at` present
- [ ] Test INSERT `ledger_read_audit` with `caller_kind='ncvet_verifier'` succeeds
- [ ] Test INSERT status snapshot with `valid_until <= signed_at` fails

## Rollback note

Prefer forward-fix. Removing `ncvet_verifier` from CHECK after G.b deploy requires coordinated code rollback.

## Related

- Design: [`docs/design/authoring_harness_phase_g.md`](../design/authoring_harness_phase_g.md) §G.b
- Prior: [`014_credential_ledger_checklist.md`](014_credential_ledger_checklist.md)
