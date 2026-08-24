# Migration 022 — SAMVAAD Formative Evidence Checklist

Apply `services/pratibimb/ledger/migrations/022_samvaad_formative_evidence.sql`
after migration **021**.

## Deploy order

1. Confirm migration 021 is applied.
2. Apply 022; it tightens the deployed 021 class CHECK and creates 022.
3. Verify `samvaad_formative_evidence` and both assessment-role CHECKs.
4. Deploy the SAMVAAD.d application build.

## Verification

- [ ] 021 admits only `machine_sim` and `preceptor_attested`
- [ ] 022 pins `assessment_kind='formative'`
- [ ] 022 admits the frozen source enum, including `patient_reported`
- [ ] Provenance columns are NOT NULL: source context, submitter, session anchor,
      matcher parameters, capture timestamp
- [ ] No UPDATE grants on either evidence table

## Rollback

App-first disable. The table and tightened summative gate are forward-only after
rows exist.
