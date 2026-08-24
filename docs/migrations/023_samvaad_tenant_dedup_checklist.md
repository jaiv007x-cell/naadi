# Migration 023 — SAMVAAD Tenant Dedup and Freshness Provenance

Apply
`services/pratibimb/ledger/migrations/023_samvaad_tenant_dedup_and_freshness.sql`
after migration **022** and before the SAMVAAD.e application build.

## Deploy order

1. Confirm migrations 021 and 022 are applied and capture writes are paused.
2. Count existing 021/022 rows. If either table is non-empty, prepare
   `samvaad_023_backfill_manifest` keyed by evidence table + evidence ID.
3. Have `role:samvaad-ledger-oncall-lead` review tenant, learner, session,
   competency-hit, and formative canonical-digest values.
4. Apply 023. Any missing manifest value halts before `NOT NULL` or UNIQUE DDL.
5. Deploy SAMVAAD.e, resume capture, and run one manual exact reconciliation.

## Verification

- [ ] 021 has non-null `learner_pseudo_id` and `session_anchor`
- [ ] 022 has non-null tenant, learner, competency hits, and evidence digest
- [ ] 021 rejects duplicate `(tenant_id, transcript_digest)`
- [ ] 022 rejects duplicate `(tenant_id, evidence_digest)`
- [ ] No tenant or learner value was inferred from submitter or transcript text
- [ ] Rebuild output is byte-identical across two runs
- [ ] Dhaara authoritative/projected counts converge exactly
- [ ] No UPDATE grants exist on either evidence table

## Rollback

Disable SAMVAAD.e capture first. Migration 023 is forward-only after new rows
exist; do not remove provenance or tenant-scoped uniqueness to roll back the
application.
