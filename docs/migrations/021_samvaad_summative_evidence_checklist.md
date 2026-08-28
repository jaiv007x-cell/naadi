# Migration 021 — SAMVAAD Summative Evidence (SAMVAAD.c) Checklist

Apply `services/pratibimb/ledger/migrations/021_samvaad_summative_evidence.sql`
**after** migrations **018**, **019**, **020**.

## Deploy order

1. Confirm **018–020** applied.
2. Apply **021** on production ledger DB.
3. Verify table `samvaad_summative_evidence` + summative role/class CHECKs.
4. Deploy SAMVAAD.c application build.

## Rollback

App-first disable; table forward-only once rows exist (same class as 019 ARP).

- [ ] `ck_samvaad_summative_assessment_kind` pins `assessment_kind='summative'`
- [ ] `ck_samvaad_summative_evidence_class` allows only `machine_sim`, `preceptor_attested`
- [ ] `patient_reported` remains excluded from 021
- [ ] No UPDATE grants on `samvaad_summative_evidence`
