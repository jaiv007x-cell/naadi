# I.c.2 — C3 / C4 ops checklist (compat coercion delete)

**Owner:** `role:authoring-platform-oncall-lead`  
**Companion:** [`credentials_regrade_observability.md`](credentials_regrade_observability.md) §Compat coercion delete · [`authoring_harness_phase_i.md`](../design/authoring_harness_phase_i.md) §I.c.2

**Scope:** **Separate deploy / separate diff** from [`i_c_ship_checklist.md`](i_c_ship_checklist.md). V2-only land uses [`i_c_2_v2_only_ship_checklist.md`](i_c_2_v2_only_ship_checklist.md). Do not bundle v2-only annotation with cutover-only ship diff (C2 independence).

## First-walk (scope open — before countersign)

- [ ] **C3 clock-start is `status-list-v2-only@…` only** — `arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)` from 2026-09-22 is **not** referenced as C3 clock-start
- [ ] **V2-only diff negative-grep (symmetric to cutover):** run against the v2-only PR **diff** scoped to `docs/ops/`, not working tree:
  ```bash
  git show <v2-only-ship-commit> -- docs/ops/ | rg "arp-surface-cutover@"
  ```
  **Must return zero matches** — mirror of cutover PR's `status-list-v2-only@` negative grep; independent PRs
- [ ] **C4 alert live before merge:** `increase(status_list_compat_coercion_total[1d]) > 0` routes to `role:authoring-platform-oncall-lead` **before v2-only deploy merges**, not after

## At v2-only land (C3 clock-start)

- [ ] v1 status-list readers retired from production
- [ ] Grafana annotation dropped **exactly**: `status-list-v2-only@<tag> (sha:<sha>)` (distinct prefix from `arp-surface-cutover@…`)
- [ ] C4 alert armed: `increase(status_list_compat_coercion_total[1d]) > 0` → ticket to authoring-platform on-call (**no deferral**)
- [ ] Record clock-start date in this file / runbook

**Clock-start date:** _______________ (from `status-list-v2-only@…` annotation timestamp only)

## During 30-day window

- [ ] C4 does not fire (any fire → **reset**: new day-0 = next true-zero day after the fire clears)
- [ ] **Window-bounded block (not time-bounded):** any in-window C4 fire invalidates the **original** 30-day window — a day-30 PR opened against the pre-reset window must be **closed or rebased**; do not merge a stale PR after reset because the calendar hit day 30
- [ ] Do **not** open day-30 PR if C4 fired in-window for the current window

## Day-30 PR (only if window clean)

- [ ] Observed zero window: from _______________ to _______________
- [ ] C4 did not fire in window
- [ ] PR deletes coercion in:
  - [ ] `services/pratibimb/credentials/status_list.py` — `normalize_status_list_entries` v1 branch
  - [ ] `services/pratibimb/audit/metrics.py` — `STATUS_LIST_COMPAT_COERCION_TOTAL`
  - [ ] related tests (enumerate mechanically — do not guess):
    ```bash
    rg -rn "normalize_status_list_entries|STATUS_LIST_COMPAT_COERCION_TOTAL|compat_coercion" services/pratibimb/tests/
    ```
- [ ] PR description cites **I.c.2 C3**, links `status-list-v2-only@…`, states observed window
