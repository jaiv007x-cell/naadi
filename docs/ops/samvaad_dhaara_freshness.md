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

## Cron path (secondary)

Daily sweep at 02:00 UTC:

1. Walk nodes with competency-specific decay classes.
2. Apply decay where `last_observed + decay_curve` exceeds threshold.
3. Emit freshness recommendations — **no synthetic evidence rows**.

## Alerts (TBD — ops baseline)

- Nodes in decay-warning band with no new evidence > N days (threshold review artifact required before production alert).
- Cron job missed / stale last-run timestamp.

## Cross-refs

- [`../samvaad.md`](../samvaad.md) — I-S-7, open question B
- [`../design/samvaad_a.md`](../design/samvaad_a.md) — SAMVAAD.a closed matrix
