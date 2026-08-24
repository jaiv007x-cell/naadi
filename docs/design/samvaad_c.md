# SAMVAAD.c — Nirikshak Matcher Merge + Ledger Summative Projector (Scope)

**Parent:** [`samvaad.md`](../samvaad.md) Framework v1  
**Predecessor:** SAMVAAD.b **closed** — `samvaad_b_acceptance` **14/14** · `samvaad_acceptance` **22/22**  
**Status:** **Fully countersigned** (2026-08-23) — `samvaad_c_acceptance` **18/18** (16 matrix rows; row **10** = **10a**+**10b**; row **7** = capture **#7** + route **#7b**) · compose **54/54** SAMVAAD  
**Framework stop rule:** inherited unchanged — no .c exceptions.

## Boundary sentence

SAMVAAD.c registers SAMVAAD matcher kinds into Nirikshak (detectors in `samvaad.runtime`), lands **`samvaad_summative_evidence` via migration 021**, stamps I-S-14 at capture, fail-closes summative on `patient_reported` (I-S-13), and opens one production Call on `POST /v1/samvaad/verify` with `SAMVAAD_VERIFIER_LIVE_EMIT` (default false).

---

## Migration **021**

| # | Owner |
|---|--------|
| 018 | SAMVAAD.a `samvaad_verifier` |
| 019 | I.a ARP |
| 020 | I.b status-list `identifier_kind` |
| **021** | **SAMVAAD.c** `samvaad_summative_evidence` |

SQL file **must** open with phase anchor comment:

```sql
-- 021_samvaad_summative_evidence.sql
-- SAMVAAD.c: summative evidence SoT table (INSERT-only)
```

Checklist: [`021_samvaad_summative_evidence_checklist.md`](../migrations/021_samvaad_summative_evidence_checklist.md)

---

## Flag semantics + rollback runbook pin

One production Call site, **always invoked**. Flag gates emission / SoT write, not invocation.

| Rollback | Action | Audit trail |
|----------|--------|-------------|
| **First response** | `SAMVAAD_VERIFIER_LIVE_EMIT=false` | Call still runs; audit stamps `live_emit=false`; forensic continuity preserved |
| **Escalation** | Remove `/v1/samvaad/verify` route (deploy) | Same as I.a 019 flag-flip → route-removal ordering |

---

## Pre-ack clarifications (pinned)

### #7 — counter shape (**labeled**, not distinct counter)

| Surface | Value |
|---------|--------|
| Counter | **`samvaad_summative_rejected_total`** (single counter family) |
| Label | **`reason="patient_reported"`** (closed enum; cardinality bounded) |
| HTTP | **422** Unprocessable Entity — semantic validation on assessment kind, **not 403** (not authz) |
| `error_kind` | `summative_evidence_class_forbidden` |

PromQL: `samvaad_summative_rejected_total{reason="patient_reported"}` — decomposable without new counter rows per reason.

### #10 — two distinct assertions (one matrix row, two test functions)

| Sub-case | Request | Assert |
|----------|---------|--------|
| **10a** | `dry_run=false` + **`SAMVAAD_LIVE_WRITE_ALLOW=true`** | Summative INSERT into 021; audit `dry_run=false`, `would_mutate=true` |
| **10b** | `dry_run=false` + **`SAMVAAD_LIVE_WRITE_ALLOW=false`** (default) | **No** INSERT; audit stamps `would_mutate=false`; Call path still exercised where applicable |

---

## Acceptance matrix (16 rows) — acked

Rows are semantic units; tests are assertion units. **16 rows / 18 tests** is the correct disagreement (row **10** = two flag positions; row **7** = capture contract + route HTTP).

| # | Sub | Case | Asserts |
|---|-----|------|---------|
| **1** | | Registry merge | `SAMVAAD_MATCHER_KINDS` registered; **`len(SAMVAAD_MATCHERS) == len(SAMVAAD_MATCHER_KINDS)` exactly** (not ≥); each delegates to `samvaad.runtime` |
| **2** | | Empathy + via Nirikshak | AND + window → hit |
| **3** | | Empathy − via Nirikshak | Incomplete steps → miss |
| **4** | | Digital Literacy + via Nirikshak | `action_sequence` match; **no separate − case** (negatives covered by empathy − and illegal_transition) |
| **5** | | Coexistence | .b golden outputs unchanged post-merge |
| **6** | | Formative `patient_reported` | `capture_formative` succeeds — **paired with #7** (evidence-class isolation) |
| **7** | **7** | Summative rejects `patient_reported` (capture) | `SummativeEvidenceRejectedError` + `error_kind` + **`samvaad_summative_rejected_total{reason="patient_reported"}`** — **paired with #6** |
| | **7b** | Summative rejects `patient_reported` (route) | `POST /v1/samvaad/verify` → **422**; `detail.error_kind=summative_evidence_class_forbidden` (observes status, not compose-from-mapping) |
| **8** | | Summative accepts machine_sim / preceptor | INSERT 021 + I-S-14 fields |
| **9** | | `dry_run` omitted | Default true; no INSERT; audit stamped |
| **10** | **10a** | `dry_run=false` + allow | INSERT; audit `would_mutate=true` |
| | **10b** | `dry_run=false` + no allow | No INSERT; audit `would_mutate=false` |
| **11** | | One AST Call | Exactly one production Call site (`verify_service.py`); failure names offending paths |
| **12** | | Flag off Calls | `live_emit=false` stamped; no summative side-effect |
| **13** | | Sink fail | 503 when sink fails with flag on |
| **14** | | Emit payload | Key-set **⊇** .b stub (superset; may add keys) |
| **15** | | `illegal_transition` | `error_kind=illegal_transition` distinct (bias ladder; not HTTP summative conflict) |
| **16** | | Compose | `samvaad_acceptance` 22 + `samvaad_b_acceptance` 14 green alongside .c |

### Named exclusions (not matrix gaps)

| Surface | Why excluded |
|---------|----------------|
| Formative via `POST /v1/samvaad/verify` (`assessment_kind=formative` + `patient_reported`) | Semantic contract proved by **#6**; route `assessment_kind` wiring carry-forward for SAMVAAD.d — **optional**, not .c blocker |
| Summative-eligible evidence on formative surface | Formative is a *superset*; no rejection contract on the permissive side |

**SAMVAAD acceptance rollup (separate from `i_acceptance`):**

| Marker | Count |
|--------|-------|
| `samvaad_acceptance` | 22 |
| `samvaad_b_acceptance` | 14 |
| `samvaad_c_acceptance` | **18** (16 matrix rows; row **10** = **10a**+**10b**; row **7** = **7**+**7b**) |
| **SAMVAAD total** | **54** |

`i_acceptance` (Phase I ARP) remains its own compose — do not conflate totals.

**Marker:** `samvaad_c_acceptance`

## Gate

**SAMVAAD.c fully countersigned** (2026-08-23) — closeout asks **#1** (row-10 table) + **#2** (route #7b) landed · **54/54** green → **SAMVAAD.d** opens on drafted scope (same review gate: scope walk → Q-sharpening → countersign → frozen matrix → code).

## Carry-forwards into SAMVAAD.d (not forgotten optionals)

| # | Item | Disposition at .d scope open |
|---|------|------------------------------|
| **CF-1** | Formative-via-route (`POST /v1/samvaad/verify` + `assessment_kind=formative` + `patient_reported`) | If .d touches the route surface → land in .d matrix. If orthogonal → either standalone .d case **or** explicit *"deferred through .d, revisit at .e"* with a named trigger. Do **not** leave as indefinite "optional." |
| **CF-2** | `samvaad_verifier` live emit — audit-row shape parity vs .b stub emit | If .d touches the verifier surface → re-verify parity as a matrix case (byte-identical to .b stub keys / shape under live path). Do **not** assume .c parity persists across a verifier change. |

### Posture at .d scope draft (not surprises)

1. **CF-1 disposition is a scope-walk question, not a post-hoc decision.** Pick `{land in .d matrix | standalone .d case | explicit defer with named trigger}` *during* the scope walk — before countersign / before code opens. No "we'll figure it out after."
2. **CF-2 parity is a matrix-case candidate if the verifier surface is touched at all.** Even a small verifier change (signature tweak, new field, reordered params) can shift audit-row byte shape and silently break .b cases that assumed the stub shape. If .d touches the verifier → parity is a **matrix row**, not a belt test.

**Resolution (2026-08-24):** CF-1 landed in **SAMVAAD.d** (022 formative table). CF-2 = **verifier orthogonality** CI row in `.d` #8. Dhaara/Postgres carry-forward → **SAMVAAD.e** ([`samvaad_e.md`](./samvaad_e.md)).

### Review gate (unchanged)

1. Scope walk — surfaces touched, invariants at stake, matrix shape proposed  
2. Q-sharpening — axis/shape questions pinned before countersign  
3. Countersign — matrix frozen, marker named, SAMVAAD-only rollup (`i_acceptance` separate)  
4. Code against frozen matrix — green before close  

