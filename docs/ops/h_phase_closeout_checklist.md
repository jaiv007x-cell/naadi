# Phase H closeout checklist

**Scope:** rung 3 (Replayable Competency Proof) sign-off — doc + ops only.  
**Design:** [`authoring_harness_phase_h.md`](../design/authoring_harness_phase_h.md) §Phase H closeout.

---

## Acceptance suite (CI / release gate)

```bash
pytest -m h_acceptance
```

**Expected:** **44/44** green (`h_acceptance` marker on H.a + H.b + H.c.1 + H.c.2 modules).

| Module | Cases |
|--------|-------|
| `test_authoring_h_a.py` | 15 |
| `test_authoring_h_b.py` | 15 |
| `test_authoring_h_c.py` | 9 |
| `test_authoring_h_c_2.py` | 5 |

Wire `pytest -m h_acceptance` into the release/CI gate that protects the rung-3 contract.

---

## Deploy order (migrations)

| Order | Checklist |
|-------|-----------|
| 016 | [`016_regrade_artifact_checklist.md`](../migrations/016_regrade_artifact_checklist.md) |
| 017 | [`017_regrade_artifact_unique_checklist.md`](../migrations/017_regrade_artifact_unique_checklist.md) |

**017 rollback:** app first, then drop UNIQUE (see checklist).

---

## Threshold review (2026-09-22) — written artifact required

- [ ] **Owner (role):** `role:authoring-platform-oncall-lead`
- [ ] **Review date:** on or before **2026-09-22** (first production-signal window)
- [ ] **Artifact landed:** PR against [`credentials_regrade_observability.md`](credentials_regrade_observability.md) with:
  - production-signal data (PromQL snapshots / page history for the review window)
  - **threshold decision per surface:** hold provisional | promote to fixed | revise
- [ ] **Deferred agenda line-items** reviewed in same cycle (async queue, score-under-different-rubric, codegen stretch) — not folded into threshold-only

**Anti-pattern:** checking "reviewed" without a merged PR — provisional must not calcify.

---

## Observability

- [ ] Runbook: [`credentials_regrade_observability.md`](credentials_regrade_observability.md)
- [ ] Five fail-closed sink-failure anchors synced with `test_authoring_h_c_2.py` registry (`test_h_c_2_5`)

---

## Explicit non-goals (frozen at H closeout)

- Score-under-different-rubric
- Async re-grade queue
- Fifth `surface` enum value
- UNIQUE widening on `regrade_artifacts`
- New F `/v1/ledger/` query kinds
