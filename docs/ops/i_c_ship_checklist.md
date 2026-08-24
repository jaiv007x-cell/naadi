# I.c ship checklist — ARP `surface` cutover (cutover-only)

Walk this list **at deploy**, not beforehand. Prose in the runbook is skimmable; these checkboxes are the deploy gate.

**Scope:** This checklist applies to the **cutover-only** ship diff (I.c.1 surface relabel). Do **not** bundle `status-list-v2-only@…` into the same diff — v2-only lands on a **subsequent deploy** with [`i_c_2_compat_delete_checklist.md`](i_c_2_compat_delete_checklist.md) (C2 independence).

**Companion:** [`credentials_regrade_observability.md`](credentials_regrade_observability.md) §I.c · [`authoring_harness_phase_i.md`](../design/authoring_harness_phase_i.md) §I.c

## Pre-deploy

- [ ] I.c.1 code is in the release (`arp/service.py` + `arp/verify_audit.py` emit `surface="arp"`)
- [ ] `pytest -m h_acceptance` **44/44** and `pytest -m i_acceptance` **58/58** green on the release commit
- [ ] Ship diff is **cutover-only** — verify mechanically:
  ```bash
  rg -n "status-list-v2-only@" docs/ops/
  ```
  **Must return no matches** in the cutover PR diff (v2-only is a separate deploy)

## At ship (C1 — three required steps)

- [ ] **1. Fill `<tag>`** in runbook §I.c **Deploy identifier** with the release tag (e.g. `v2.14.0`)
- [ ] **2. Fill `<sha>`** in runbook §I.c **Deploy identifier** with the full git commit SHA of the cutover deploy
- [ ] **3. Drop Grafana annotation** with text **exactly** (literal prefix — no near-variants):
  ```text
  arp-surface-cutover@<tag> (sha:<sha>)
  ```
  at the deploy timestamp (hover-legible — tag + SHA in the annotation body, not bare timestamp)

### Annotation exact-match walk (reviewer gate)

Template in runbook (must match character-for-character except filled tag/sha):

```text
arp-surface-cutover@<tag> (sha:<sha>)
```

**Invalid near-variants:** `arp-cutover@…`, `arp_surface_cutover@…`, `surface-cutover@…`, bare timestamp only.

Mechanical grep on the ship diff / runbook fill-in:

```bash
rg -n "arp-surface-cutover@" docs/ops/credentials_regrade_observability.md
```

Filled value must match `arp-surface-cutover@<actual-tag> (sha:<actual-sha>)` — reviewer confirms no alternate prefix.

After the three steps, replace `TBD at ship` in the runbook cutover table with the filled values. **PR description must include this checklist with all boxes ticked** — a bare link to the checklist without ticked boxes is **not** a walk and does not clear the gate.

## Post-deploy smoke

- [ ] `rate(audit_sink_failure_total{surface="arp"}[5m])` scrapes (may be zero — series exists)
- [ ] H regrade still labeled `surface="regrade"` only (mint/verify fail-closed under load test or greps)
- [ ] No new ARP fail-closed samples under `surface="regrade"` after cutover timestamp

## Do not

- Rewrite historical Prometheus samples
- Start C3 compat-delete clock from `arp-surface-cutover@…` — use **`status-list-v2-only@…`** only
- Start C3 clock from first accidental zero while v1 readers remain deployed

## Related — v2-only (separate deploy; not this checklist)

**Do not** include `status-list-v2-only@…` in the cutover ship diff. When v1 status-list readers are retired on a **later deploy**, use [`i_c_2_compat_delete_checklist.md`](i_c_2_compat_delete_checklist.md) and drop:

```text
status-list-v2-only@<tag> (sha:<sha>)
```

That annotation (not `arp-surface-cutover@…`) starts the 30-day C3 zero window. May share a calendar day with cutover; must **not** share the same diff/annotation bundle.
