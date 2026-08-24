# I.c.2 v2-only ship checklist — status-list reader retirement (v2-only diff)

Walk this list **at v2-only deploy**, not beforehand. Separate deploy / separate diff from [`i_c_ship_checklist.md`](i_c_ship_checklist.md) (cutover already merged).

**Scope:** Retire v1 status-list **readers** (reject missing-kind / v1-only envelopes); start C3 clock. **Do not** bundle day-30 compat-delete code removal — that is a **later PR** after 30 consecutive zero days ([`i_c_2_compat_delete_checklist.md`](i_c_2_compat_delete_checklist.md)).

**Companion:** [`credentials_regrade_observability.md`](credentials_regrade_observability.md) §Compat coercion delete · [`authoring_harness_phase_i.md`](../design/authoring_harness_phase_i.md) §I.c.2

## First-walk (scope open — before countersign)

- [ ] **C3 clock-start is `status-list-v2-only@…` only** — `arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)` is **not** referenced as C3 clock-start
- [ ] **V2-only diff negative-grep (symmetric to cutover):**
  ```bash
  git show <v2-only-ship-commit> -- docs/ops/ | rg "arp-surface-cutover@"
  ```
  **Must return zero matches**
- [ ] **C4 alert live before merge:** `increase(status_list_compat_coercion_total[1d]) > 0` → `role:authoring-platform-oncall-lead` **before v2-only deploy merges**, not after

## Pre-deploy

- [ ] Product diff retires v1 readers (see Phase I §I.c.2.1 product pins) — publish `status_list.v2`; read paths reject v1 / missing-kind **without coercion**
- [ ] `pytest -m h_acceptance` **44/44** and `pytest -m i_acceptance` green on release commit
- [ ] Ship diff is **v2-only** — verify mechanically:
  ```bash
  git show <v2-only-ship-commit> | rg "normalize_status_list_entries|STATUS_LIST_COMPAT_COERCION_TOTAL"
  ```
  **Must return no deletion/removal matches** (compat shim stays until day-30 PR)
- [ ] Ship diff does **not** remove compat coercion code (day-30 gated)

## At ship (three required steps)

- [ ] **1. Fill `<tag>`** in runbook §Compat coercion delete **Deploy identifier (v2-only)** with release tag
- [ ] **2. Fill `<sha>`** with full git commit SHA of the v2-only deploy
- [ ] **3. Drop Grafana annotation** with text **exactly** (literal prefix — no near-variants):
  ```text
  status-list-v2-only@<tag> (sha:<sha>)
  ```
  at deploy timestamp — **sole C3 clock-start** (not `arp-surface-cutover@…`)

### Annotation exact-match walk (reviewer gate)

Template:

```text
status-list-v2-only@<tag> (sha:<sha>)
```

**Invalid near-variants:** `status_list_v2_only@…`, `status-list-v2@…`, `v2-only@…`, bare timestamp only.

Mechanical grep on ship diff / runbook fill-in:

```bash
rg -n "status-list-v2-only@" docs/ops/credentials_regrade_observability.md
```

Filled value must match `status-list-v2-only@<actual-tag> (sha:<actual-sha>)`.

**PR description must include this checklist with all boxes ticked** — bare link ≠ walk.

## Post-deploy smoke

- [ ] New publishes use `status_list.v2` with `identifier_kind` on every entry
- [ ] v1 envelope or missing-kind entry **rejects** (no silent coercion; counter must not increment on happy path)
- [ ] C4 alert armed and routes to `role:authoring-platform-oncall-lead`

## Do not

- Start C3 clock from `arp-surface-cutover@…`
- Start C3 from first accidental zero while v1 readers still deployed
- Open day-30 delete PR in same diff as v2-only land
- Bundle `arp-surface-cutover@…` changes in this PR

## Related — cutover (already merged)

Cutover annotation `arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)` is observability-only — **does not** start C3. May share a calendar day with v2-only; must **not** share diff or annotation bundle.

After v2-only land, continue with [`i_c_2_compat_delete_checklist.md`](i_c_2_compat_delete_checklist.md) (30-day window → day-30 delete PR).
