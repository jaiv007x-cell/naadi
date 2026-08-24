# I.c.2 v2-only ship checklist — status-list reader retirement (v2-only diff)

Walk this list **at v2-only deploy**, not beforehand. Separate deploy / separate diff from [`i_c_ship_checklist.md`](i_c_ship_checklist.md) (cutover already merged).

**Scope:** Retire v1 status-list **readers** (reject missing-kind / v1-only envelopes on ARP path); start **C3 clock** via `status-list-v2-only@…` only. **Do not** bundle day-30 compat-delete code removal.

**Companion:** [`credentials_regrade_observability.md`](credentials_regrade_observability.md) §Compat coercion delete · [`authoring_harness_phase_i.md`](../design/authoring_harness_phase_i.md) §I.c.2.1

## Symmetric negative greps (both directions — either contamination fails walk)

| PR | Diff grep | Expected |
|----|-----------|----------|
| **Cutover** (merged) | `git show 9c71dd4 -- docs/ops/ \| rg "status-list-v2-only@"` | **zero** |
| **V2-only** (this PR) | `git show <v2-only-ship-commit> -- docs/ops/ \| rg "arp-surface-cutover@"` | **zero** |
| **Day-30 delete** (later) | `git show <day-30-commit> -- docs/ops/ \| rg "status-list-v2-only@|arp-surface-cutover@"` | **zero** (no annotation churn) |

## First-walk (before countersign)

- [ ] **C3 clock-start ≠ cutover annotation** — `arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)` is **not** C3 clock-start; C3 starts only at **`status-list-v2-only@<tag> (sha:<sha>)`** dropped at I.c.2.1 deploy
- [ ] **V2-only diff negative-grep:** `git show <v2-only-ship-commit> -- docs/ops/ | rg "arp-surface-cutover@"` → **zero**
- [ ] **C4 live before merge:** `increase(status_list_compat_coercion_total[1d]) > 0` → `role:authoring-platform-oncall-lead`; **test-page evidence** in PR description (screenshot or page-log timestamp — not declared-only)

## Pre-deploy

- [ ] P1–P3 product pins landed (§I.c.2.1)
- [ ] **P1 sole emit-site grep** (production mint path — post-P1 must be v2-only):
  ```bash
  rg -n "STATUS_LIST_SCHEMA_V1|status_list\.v1" \
    services/pratibimb/credentials/status_service.py \
    services/pratibimb/credentials/status_list.py
  ```
  **Expected after P1:** zero hits in `status_service.py`; constants/shim references in `status_list.py` / `compat.py` allowed until day-30
- [ ] `pytest -m h_acceptance` **44/44** · `pytest -m i_acceptance` green (base **58** unchanged) · `pytest -m i_c_2_acceptance` green
- [ ] **No compat-delete in diff:**
  ```bash
  git show <v2-only-ship-commit> | rg "normalize_status_list_entries|STATUS_LIST_COMPAT_COERCION_TOTAL"
  ```
  Must show **no deletion/removal** lines (shim retained until day-30 PR)

## At ship (C3 clock-start — three required steps)

- [ ] **1. Fill `<tag>`** in runbook §V2-only deploy identifier
- [ ] **2. Fill `<sha>`** with full git commit SHA of v2-only deploy
- [ ] **3. Drop Grafana annotation** — **sole C3 clock-start** — text **exactly**:
  ```text
  status-list-v2-only@<tag> (sha:<sha>)
  ```
  Distinct from cutover annotation `arp-surface-cutover@…` (may share calendar day; **not** same diff/bundle)

### Annotation exact-match walk

Template: `status-list-v2-only@<tag> (sha:<sha>)`

**Invalid near-variants:** `status_list_v2_only@…`, `status-list-v2@…`, `v2-only@…`, bare timestamp.

```bash
rg -n "status-list-v2-only@" docs/ops/credentials_regrade_observability.md
```

**PR description must include this checklist with all boxes ticked.**

## Post-deploy smoke

- [ ] Mint publishes `status_list.v2` with `identifier_kind` on every entry
- [ ] ARP verify: v1 / missing-kind → fail-closed; **no** `status_list_compat_coercion_total` increment on ARP path
- [ ] Credentials verify: v2 accepted; v1 accepted **via shim only** (`credentials/compat.py`)
- [ ] C4 alert routes to `role:authoring-platform-oncall-lead`

## I.c.2.1 belt cases (`pytest -m i_c_2_acceptance` — distinct from `i_acceptance`)

| # | Case | Module |
|---|------|--------|
| **B1** | V2-only annotation template + invalid near-variants | `test_authoring_i_c_2_v2_only_ship_checklist.py` |
| **B2** | Symmetric negative grep pin (`arp-surface-cutover@` absent from v2-only checklist scope) | same |
| **B3** | No compat-delete / no coercion removal in v2-only ship scope | same |
| **B4** | ARP v1 fixture → fail-closed + coercion counter unchanged | `test_authoring_i_c_2.py` |

## Do not

- Start C3 from `arp-surface-cutover@…` or first accidental zero during coexistence
- Bundle cutover annotation changes or day-30 shim deletion in this PR
- Rewrite historical `envelope_bytes` (P5)

## Related

- Cutover: [`i_c_ship_checklist.md`](i_c_ship_checklist.md) — merged
- C3 window + day-30 delete: [`i_c_2_compat_delete_checklist.md`](i_c_2_compat_delete_checklist.md)
