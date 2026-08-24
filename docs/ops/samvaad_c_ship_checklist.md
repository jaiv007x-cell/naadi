# SAMVAAD.c ship checklist — summative verify + migration 021

**Countersign:** SAMVAAD.c **fully countersigned 2026-08-23** · `samvaad_c_acceptance` **18/18** · SAMVAAD compose **54/54**.

Walk at deploy. Prose in design docs is skimmable; these checkboxes are the deploy gate.

**Scope:** SAMVAAD.c application + migration **021** (`samvaad_summative_evidence`). Do **not** bundle SAMVAAD.d (Dhaara event path, Postgres projector hardening) into this ship.

**Companion:** [`021_samvaad_summative_evidence_checklist.md`](../migrations/021_samvaad_summative_evidence_checklist.md) · [`design/samvaad_c.md`](../design/samvaad_c.md)

## Pre-deploy

- [ ] Migrations **018–020** applied on ledger DB
- [ ] `pytest -m samvaad_c_acceptance` **18/18** green on release commit
- [ ] `pytest -m "samvaad_acceptance or samvaad_b_acceptance or samvaad_c_acceptance"` **54/54** green on release commit

## At ship

- [ ] Apply migration **021** — `021_samvaad_summative_evidence.sql`
- [ ] Verify `samvaad_summative_evidence` + `ck_samvaad_evidence_class` + INSERT-only grants
- [ ] Deploy application build with `POST /v1/samvaad/verify` route
- [ ] Confirm defaults: `SAMVAAD_VERIFIER_LIVE_EMIT=false`, `SAMVAAD_LIVE_WRITE_ALLOW=false`

## Post-deploy smoke

- [ ] `POST /v1/samvaad/verify` with omitted `dry_run` → **200**, no ledger INSERT, audit emit with `live_emit=false`
- [ ] `patient_reported` on summative path → **422**, `error_kind=summative_evidence_class_forbidden`
- [ ] `samvaad_summative_rejected_total{reason="patient_reported"}` series scrapes (may be zero)

## Rollback (three-tier — same as design pin)

1. **First:** `SAMVAAD_VERIFIER_LIVE_EMIT=false` — Call continues; audit stamps `live_emit=false`; no INSERT
2. **Table:** forward-only once rows exist — do not drop `samvaad_summative_evidence` if referenced downstream
3. **Escalation:** remove `/v1/samvaad/verify` route (deploy)

## Do not

- Enable `SAMVAAD_LIVE_WRITE_ALLOW=true` without explicit ops sign-off
- Conflate SAMVAAD rollup (54) with `i_acceptance` in release notes
