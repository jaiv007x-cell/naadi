# I.c ship checklist — ARP `surface` cutover (cutover-only)

**Countersign:** cutover PR **merged 2026-08-24**. Pre-deploy + fill complete. Remaining: Grafana drop + post-deploy smoke at **2026-09-22** (ops execution; no review gate).

Walk this list **at deploy**, not beforehand. Prose in the runbook is skimmable; these checkboxes are the deploy gate.

**Scope:** This checklist applies to the **cutover-only** ship diff (I.c.1 surface relabel). Do **not** bundle `status-list-v2-only@…` into the same diff — v2-only lands on a **subsequent deploy** with [`i_c_2_compat_delete_checklist.md`](i_c_2_compat_delete_checklist.md) (C2 independence).

**Companion:** [`credentials_regrade_observability.md`](credentials_regrade_observability.md) §I.c · [`authoring_harness_phase_i.md`](../design/authoring_harness_phase_i.md) §I.c

## Pre-deploy

- [x] I.c.1 code is in the release (`arp/service.py` + `arp/verify_audit.py` emit `surface="arp"`)
- [x] `pytest -m h_acceptance` **44/44** and `pytest -m i_acceptance` **58/58** green on the release commit (I.c.1 closeout; tip **61** with ship-checklist meta)
- [x] Ship diff is **cutover-only** — `git show 9c71dd4` has zero `status-list-v2-only@` matches

## At ship (C1 — three required steps)

- [x] **1. Fill `<tag>`** → `v2.14.0`
- [x] **2. Fill `<sha>`** → `f1fe486fe9fb048ac982e0a4d4b49ae293cb9797`
- [ ] **3. Drop Grafana annotation** with text **exactly** (ops at 2026-09-22 deploy timestamp):
  ```text
  arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)
  ```
  at the deploy timestamp (hover-legible — tag + SHA in the annotation body, not bare timestamp)

### Annotation exact-match walk (reviewer gate) — **cleared 2026-08-24**

Filled emit site matches character-for-character:

```text
arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)
```

**Invalid near-variants:** `arp-cutover@…`, `arp_surface_cutover@…`, `surface-cutover@…`, bare timestamp only.

## Post-deploy smoke (ops — 2026-09-22)

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
