# Migration 019 — ARP Artifact (I.a) Checklist

Apply `services/pratibimb/ledger/migrations/019_arp_artifact.sql`
**before** deploying `/v1/arp/` or any emit of `caller_kind='ncvet_recompute'`.

**018 is Samvaad** (`samvaad_verifier`) — do not reuse that number for ARP.

## Deploy order (required)

1. Confirm migrations **016–018** applied.
2. Apply migration **019** on production ledger DB.
3. Verify `ck_audit_caller_kind` includes **`ncvet_recompute`** and **`ncvet_arp_verifier`** (reserved).
4. Verify tables `arp_audit`, `arp_artifacts`, `arp_recompute_eligible_rubrics` exist.
5. Verify UNIQUE `(tenant_id, session_id, transcript_digest, alternate_rubric_id)` on `arp_artifacts`.
6. Deploy application build containing I.a ARP routes + `KNOWN_AUDIT_CALLER_KINDS` extension.

Do not reverse this order. Same-commit: app enum + emitters + migration.

## Rollback order

1. **App first** — stop minting into ARP UNIQUE space.
2. **Schema rollback is forward-only once any ARP row exists** — preferred path (same class as regrade artifacts).

| Status | Rule |
|--------|------|
| **Forward-only (preferred)** | After **any** row in `arp_audit` or `arp_artifacts`, treat **019 as irreversible** without an explicit data-migration plan. ARP artifacts are **authoritative output** — silent DELETE on rollback is forbidden. |
| **Operational path after first ARP row** | If 019 must be “rolled back” after an ARP row lands, the rollback is **app-layer disable** only — **not** schema reversal. Keep migration **019 applied**; do not DROP tables or narrow CHECK. **Two vectors (ordered):** (1) **First-response — feature-flag flip** (reversible, low-risk: stop accepting new ARP rows without a deploy). (2) **Escalation — route removal** (needs deploy: `/v1/arp/` gone from the build). Same ordering discipline as 017 app-first rollback. |
| **Pre-first-row only** | If no ARP rows were ever written, rollback may drop ARP tables + narrow `ck_audit_caller_kind` (remove `ncvet_recompute`, `ncvet_arp_verifier`) in a controlled maintenance window. |
| **Never** | Drop CHECK / tables while app still emits `ncvet_recompute` — same discipline as 017. Silent DELETE of `arp_*` rows as part of rollback is forbidden. |

If production must revert app while ARP rows exist: **keep 019 applied**; disable routes in app only. Data retention follows ops policy; do not auto-purge `arp_*` as part of schema rollback.

## Closed caller_kind set (app + DB must match after 019)

```text
… ncvet_regrader, samvaad_verifier, ncvet_recompute, ncvet_arp_verifier
```

`ncvet_arp_verifier` is CHECK-reserved in 019; ledger_read emitters land in **I.b**.

## Pre-flight

- [ ] Backup production ledger DB
- [ ] `LEDGER_DATABASE_URL` reachable
- [ ] 018 applied
- [ ] App release includes `ncvet_recompute` in `caller_kinds.py`

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/019_arp_artifact.sql
```

## Post-flight

- [ ] CHECK includes `ncvet_recompute`, `ncvet_arp_verifier`
- [ ] `SELECT` against `arp_artifacts` UNIQUE constraint name present
- [ ] `pytest -m h_acceptance` still **44/44** (fail-fast before I suite)

## Design

- [`authoring_harness_phase_i.md`](../design/authoring_harness_phase_i.md) §I.a
