# Migration 020 — Status-list `identifier_kind` (I.b) Checklist

Apply `services/pratibimb/ledger/migrations/020_status_list_identifier_kind.sql`
**before** deploying I.b.1 code that publishes `status_list.v2` entries or writes
`identifier_kind` ∈ `{credential, regrade, arp}` into the normalized registry.

## Constraint shape (pinned)

```text
PRIMARY KEY / UNIQUE (tenant_id, identifier_kind, identifier_id)
CHECK (identifier_kind IN ('credential', 'regrade', 'arp'))
```

| Implication | Pin |
|-------------|-----|
| Closed enum | **DB CHECK** + app enum (012/016 discipline) — not app-only |
| Cross-kind same id | Same `identifier_id` under `regrade` and `arp` (or `credential`) **accepted** — no collision |
| Same-kind duplicate | Second INSERT same triple → constraint violation |
| Bogus kind | `identifier_kind='bogus'` → **CHECK violation** (not silent accept) |

## Backfill policy (named)

| Path | Pin |
|------|-----|
| **020 apply** | Backfill revoked `credential_ledger` rows → `identifier_kind='credential'` (non-null) |
| **New publishes** | `status_list.v2` — `identifier_kind` **required** on every entry |
| **Historical snapshots** | Existing `envelope_bytes` unchanged; read path for `status_list.v1` without `identifier_kind` maps to **`credential`** for **compat window only** |
| **Compat-window closure (version-based)** | Coercion is a **v1-reader** concession. Window ends when **v1 readers are retired** (v2-only deploy rejects missing-kind). Not date-based; not count-based. |
| **Delete-coercion trigger (calendar-anchored)** | After **v2-only readers land**: remove coercion path when `status_list_compat_coercion_total` stays **0 for 30 consecutive days**. Clock does **not** start from first accidental zero while v1 readers remain deployed. |
| **Observability** | Each missing-kind → `credential` coercion increments **`status_list_compat_coercion_total{from_kind="missing",to_kind="credential"}`** — silent coercion forbidden |
| **Not** | Relying on missing-field default forever without backfill / without a named close trigger |

## UNIQUE / CHECK shape (exact column tuple)

```text
PRIMARY KEY (tenant_id, identifier_kind, identifier_id)
CHECK (identifier_kind IN ('credential', 'regrade', 'arp'))
```

**Not** `(tenant_id, session_id, transcript_digest, …)` — that tuple is ARP/RCP *artifact* UNIQUE (019 / 017). Status-list coexistence is **kind-discriminated id**, not session/transcript.

## Invariants index — two UNIQUE constraints (do not conflate)

| Migration | Table | UNIQUE / PK tuple | Semantics |
|-----------|-------|-------------------|-----------|
| **019** | `arp_artifacts` | `(tenant_id, session_id, transcript_digest, alternate_rubric_id)` | At most one ARP artifact per sealed transcript + alternate rubric |
| **017** | `regrade_artifacts` | `(tenant_id, session_id, transcript_digest)` | At most one RCP per sealed transcript |
| **020** | `status_list_identifier` | `(tenant_id, identifier_kind, identifier_id)` | Revocation registry — same opaque id may appear under different kinds |

Artifact UNIQUE and identifier UNIQUE are **independent** — no shared column tuple, no cross-table coupling.

## Deploy order (required)

1. Confirm migrations **015–019** applied.
2. Apply migration **020** on production ledger DB.
3. Verify table `status_list_identifier` exists.
4. Verify CHECK `ck_status_list_identifier_kind` and PK `(tenant_id, identifier_kind, identifier_id)`.
5. Verify backfill: revoked credentials appear as `identifier_kind='credential'`.
6. Deploy I.b.1 only after this migration (verify routes + emitters + 9b flip = **same commit** as C2).

Do not reverse this order.

## Rollback order (required — mirror of deploy)

1. **App first** — stop publishing `status_list.v2` / writing non-credential kinds.
2. Then drop table only if **no** non-credential rows exist and ops accepts losing the registry.

Prefer forward-fix. After any `arp` / `regrade` row: treat 020 as **forward-only** without an explicit data-migration plan (same class as 019 ARP artifacts).

## Pre-flight

- [ ] Backup production ledger DB
- [ ] `LEDGER_DATABASE_URL` reachable
- [ ] 015 + 019 applied
- [ ] No planned silent DELETE of status-list history

## Apply

```bash
psql "$LEDGER_DATABASE_URL" -f services/pratibimb/ledger/migrations/020_status_list_identifier_kind.sql
```

## Post-flight

- [ ] `\d status_list_identifier` shows PK + CHECK
- [ ] `INSERT … identifier_kind='bogus'` fails at CHECK
- [ ] Two rows same `identifier_id`, kinds `regrade` + `arp`, same tenant — both succeed
- [ ] `pytest -m h_acceptance` still **44/44**
- [ ] I.a belt still **28/28** (`test_i_a_9b` still zero-emit until I.b.1)

## Related

- Design: [`authoring_harness_phase_i.md`](../design/authoring_harness_phase_i.md) §I.b
- Prior: [`015_credential_status_list_checklist.md`](015_credential_status_list_checklist.md), [`019_arp_artifact_checklist.md`](019_arp_artifact_checklist.md)
- Staleness inherits G.b Option A — [`authoring_harness_phase_g.md`](../design/authoring_harness_phase_g.md) §G.b “List-fetch / staleness policy (pin 3)”
