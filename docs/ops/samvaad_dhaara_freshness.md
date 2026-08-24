# SAMVAAD · Dhaara Freshness & Decay Ops

**Scope:** I-S-7(B) — `event_on_evidence + daily_decay_cron`  
**Axioms:** event path updates freshness at evidence landing; cron path applies decay only (never invents evidence)

## Cron discipline (pinned)

| Field | Value |
|-------|-------|
| **Timezone** | **UTC only** — not local hospital time |
| **Schedule** | `0 2 * * *` (02:00 UTC daily) |
| **Constant** | `DHAARA_DECAY_CRON_TIMEZONE` / `DHAARA_DECAY_CRON_SCHEDULE` in `samvaad/summative_contract.py` |

Decay curves must be identical across deployment regions. Do not anchor cron to `Asia/Kolkata` or tenant-local TZ without a design review that opens cross-region freshness divergence.

## Event path (primary)

On any new evidence row attached to a competency node:

1. Recompute freshness for that node immediately.
2. Propagate to dependent nodes per Dhaara edge rules.

Evidence storage commits before the external Dhaara publish. 021/022 remain
authoritative for existence; Dhaara absence means “not yet projected,” not
“evidence absent.”

## Projection reconciliation (SAMVAAD.e)

| Field | Value |
|-------|-------|
| **Mode** | Scheduled/startup reconciliation — never per request |
| **Environment** | `SAMVAAD_DHAARA_RECONCILE_INTERVAL_SECONDS` |
| **Default** | **60 seconds** |
| **Clamp** | **15–600 seconds** |
| **Healthy staleness bound** | **≤ 2 configured intervals** (120 seconds at default) |
| **Authority** | 021/022 evidence rows |
| **Convergence** | Exact event-count and deterministic-payload match |

The 15-second floor prevents reconciler load from becoming source-of-truth
pressure. The 600-second ceiling prevents an operator from configuring an
open-ended drift window. The health bound is always `2 ×` the effective
configured interval, not `2 ×` the default.

The reconciler rebuilds through the same snapshot builder as the post-commit
hook. Missing events are published; current events are `skipped`; payload drift
is an error. A healthy consumer may assume projection catches up within two
configured intervals. Beyond that bound, treat freshness as stale and alert;
never reinterpret Dhaara absence as evidence absence.

Health metrics:

- `samvaad_dhaara_reconcile_last_success_timestamp_seconds{assessment_kind}`
- `samvaad_dhaara_projection_stale_seconds{assessment_kind}`
- `samvaad_dhaara_reconcile_total{assessment_kind,outcome}`

## Cron path (secondary)

Daily sweep at 02:00 UTC:

1. Walk nodes with competency-specific decay classes.
2. Apply decay where `last_observed + decay_curve` exceeds threshold.
3. Emit freshness recommendations — **no synthetic evidence rows**.

## Alerts and on-call action

- Nodes in decay-warning band with no new evidence > N days (threshold review artifact required before production alert).
- Cron job missed / stale last-run timestamp.
- Dhaara projection remains stale beyond two configured reconciliation
  intervals, or authoritative/projected counts fail exact convergence.

On projection breach, `role:samvaad-ledger-oncall-lead` must:

1. Confirm 021/022 remain writable and authoritative counts are stable.
2. Pause any I-S-13 lift evidence export; do not block capture writes.
3. Run one manual reconciliation using the same snapshot builder.
4. If skew remains, preserve the divergent evidence IDs and escalate as a
   projector defect; never delete or rewrite authoritative evidence to make
   counts match.

## Cross-refs

- [`../samvaad.md`](../samvaad.md) — I-S-7, open question B
- [`../design/samvaad_a.md`](../design/samvaad_a.md) — SAMVAAD.a closed matrix
