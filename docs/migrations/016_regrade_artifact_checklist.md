# Migration 016 — Regrade Artifact (H.a) Checklist

Apply `services/pratibimb/ledger/migrations/016_regrade_artifact.sql`
**before** deploying code that writes `caller_kind='ncvet_regrader'` or
serves `/v1/regrade/` paths.

If the app deploys first, regrade audit / CHECK inserts fail.

## Deploy order (required)

1. Confirm migrations **012**–**015** applied.
2. Apply migration **016** on production ledger DB.
3. Verify `ck_audit_caller_kind` includes **`ncvet_regrader`**.
4. Verify tables `session_transcript_ledger`, `runtime.session_transcript_projection`,
   `regrade_audit`, `regrade_artifacts` exist.
5. Deploy application build containing H.a regrade routes.

Do not reverse this order. **Same-commit discipline:** app `KNOWN_AUDIT_CALLER_KINDS`
must gain `ncvet_regrader` in the same release as this migration and the emitters.

## Closed caller_kind set (app + DB must match)

```text
preceptor, analyst, service, authoring,
ncvet_audit, ncvet_issuer, ncvet_verifier, ncvet_regrader
```

## Pre-flight

- [ ] Backup production ledger DB
- [ ] Confirm `LEDGER_DATABASE_URL` reachable
- [ ] Migration 015 applied
- [ ] App about to deploy includes `ncvet_regrader` in `caller_kinds.py`

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/016_regrade_artifact.sql
```

## Post-flight verification

- [ ] `ck_audit_caller_kind` allows prior kinds + **`ncvet_regrader`**
- [ ] `regrade_audit` CHECK requires `caller_kind='ncvet_regrader'`
- [ ] `regrade_artifacts` CHECK `valid_until > signed_at`
- [ ] Test INSERT `regrade_audit` with wrong caller_kind fails
- [ ] Test INSERT artifact with `valid_until <= signed_at` fails

## Rollback note

Prefer forward-fix. Removing `ncvet_regrader` after H.a deploy requires coordinated code rollback.

## Related

- Design: [`docs/design/authoring_harness_phase_h.md`](../design/authoring_harness_phase_h.md)
- Prior: [`015_credential_status_list_checklist.md`](015_credential_status_list_checklist.md)
