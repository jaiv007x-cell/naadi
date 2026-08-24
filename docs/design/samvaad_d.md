# SAMVAAD.d — Matcher Params · I-S-13 Capture · Time-on-Task · Runtime Bias (Scope)

**Parent:** [`samvaad.md`](../samvaad.md) Framework v1  
**Predecessor:** SAMVAAD.c **fully countersigned** — `samvaad_c_acceptance` **18/18** · SAMVAAD compose **54/54**  
**Status:** **Closed** — `samvaad_d_acceptance` **11/11** · prior compose **54/54** · SAMVAAD compose **65/65**
**Framework stop rule:** inherited unchanged — no .d exceptions.

**Fork resolution (2026-08-24):** Prior Dhaara/Postgres draft moved to [`samvaad_e.md`](./samvaad_e.md). This doc is canonical for `.d`.

## Boundary sentence

SAMVAAD.d extends existing matcher **parameters** (no new matcher kinds), lands **I-S-13 capture scaffolding** via migration **022** (`samvaad_formative_evidence`), wires **runtime bias-ladder enforcement** (`illegal_transition_detector.py`), and instruments **Phase 1 time-on-task** metrics. Summative promotion of `patient_reported` stays closed until [`I-S-13_lift_trigger.md`](../deferred/I-S-13_lift_trigger.md) + Framework pin.

---

## Surfaces touched · orthogonality

| Surface | .d change | Prior-slice adjacency |
|---------|-----------|------------------------|
| `samvaad/runtime.py` | Param extensions: `window_ms`, `allow_implicit_cue`, `min_occurrences_per_turn` | .b #3/#4 empathy window; .b #14 rubric parity |
| `samvaad/nirikshak_matchers.py` | Pass params through to runtime | .c registry merge #1–#5 |
| `samvaad/illegal_transition_detector.py` | **New** — runtime I-S-11 ladder enforcement | .a #22 · .b #9 · .c #15 `illegal_transition` |
| `samvaad/formative_projector.py` + **022** | Postgres formative table; `patient_reported` capture | .c #6/#7 evidence-class pair |
| `samvaad/time_on_task.py` | Per-session record + 30-day rolling aggregation | Phase 1 pilot ([`samvaad.md`](../samvaad.md)) |
| `samvaad/verifier_emit.py` / `verify_service.py` | Emit shape unchanged (CF-2) | .c #11 exactly-one Call |

**Does not touch:** ARP surface · authoring harness · `i_acceptance` · Dhaara wiring (→ `.e`). Migration **021** changes are limited to the structural summative role/class CHECKs required by CF-1.

---

## Q-sharpening — pinned

| # | Pin |
|---|-----|
| **Q1** | I-S-13 **summative lift** deferred — trigger doc [`I-S-13_lift_trigger.md`](../deferred/I-S-13_lift_trigger.md); **condition-only**, no calendar fallback |
| **Q2** | **No new matchers** — parameter extensions on existing kinds only. `allow_implicit_cue` default **false**; `min_occurrences_per_turn` default **1**; `window_ms` continues .b discipline |
| **Q3** | **No new `evidence_class` values** — enum stays `{machine_sim, preceptor_attested, patient_reported}`; lift via I-S-13 trigger only |
| **Q4** | **Runtime bias detector** — `illegal_transition_detector.py`; **`error_kind=illegal_transition`** (same constant as `bias_remediation.py`) |
| **Q5** | **Time-on-task** — observability-only recording + 30-day rolling aggregation; **not** an eligibility predicate or bias-ladder input; **no** Phase 2 marketing unblock |
| **Q6** | **Strictly additive** — prior markers untouched; compose row asserts literal counts |

`min_occurrences_per_turn=0` is rejected while rubric/config objects are parsed,
before matcher evaluation or request traffic.

---

## CF-1 — formative capture storage (**shape (a) pinned**)

| Decision | Pin |
|----------|-----|
| **Storage** | **New migration 022** — `samvaad_formative_evidence` (formative-authoritative) |
| **Not** | Weakening 021 with `summative_eligible` column — 021 stays summative-authoritative |
| **Capture** | `patient_reported` + required provenance (`source_context`, `submitted_by`, `session_anchor`, matcher parameters) → **022 only** |
| **Summative gate** | Closed until I-S-13 trigger + Framework pin |
| **Route** | `POST /v1/samvaad/verify` + `assessment_kind=formative` lands in matrix row **#4** |

`evidence_class` remains the frozen source enum from Q3. Structural table-role
checks therefore use stored `assessment_kind='formative'` on 022 and
`assessment_kind='summative'` on 021; the 021 `evidence_class` CHECK admits only
`machine_sim` and `preceptor_attested`, while 022 admits the full frozen enum.
Summative reads additionally apply the positive eligible-class filter.

SQL header discipline (same as 021):

```sql
-- 022_samvaad_formative_evidence.sql
-- SAMVAAD.d: formative evidence capture (INSERT-only; patient_reported permitted)
```

---

## CF-2 — verifier orthogonality (**CI matrix row**)

`.d` touches **zero** lines of `samvaad/verifier_emit.py` and does not add production Call sites.

Matrix row **#8** includes the AST guard: expected Call-site count is **`== 1`**
through dispatch reuse, with the unchanged site set `verify_service.py` only.

---

## Acceptance matrix (8 rows) — frozen

**Disagreement line:** *8 rows / 11 tests — rows 1, 4, and 6 span
sub-assertions (1a/1b, 4a/4b, 6a/6b); row 3 spans three assertions in one test
function; remaining rows are one row = one test.*

| # | Sub | Case | Asserts |
|---|-----|------|---------|
| **1** | **1a** | Empathy window — in bound | `cue_onset_ms <= t <= cue_onset_ms + window_ms` → hit at **`T + window_ms`** (inclusive upper bound per `runtime.py`) |
| | **1b** | Empathy window — out bound | **`T + window_ms + 1`** → miss |
| **2** | | Implicit cue rejection | `allow_implicit_cue=false` (default): throwaway *"I'm sorry"* / *"my bad"* does **not** pass; genuine explicit cue in same session **does** pass |
| **3** | | `min_occurrences_per_turn` | At **N**: pass; at **N+1** required config: fail; **`N=0` rejected at validation** (invalid config, not “always pass”) |
| **4** | **4a** | Formative capture (022) | `patient_reported` INSERT into **022** with provenance fields; **no** 021 row |
| | **4b** | Summative read isolation | Summative/grade read uses **positive filter** on eligible classes — `patient_reported` excluded **by construction** (not empty-query accident) |
| **5** | | Runtime bias ladder | `illegal_transition_detector` rejects illegal move; **`error_kind=illegal_transition`** via `BiasRemediationError` |
| **6** | **6a** | Time-on-task per session | Session duration recorded |
| | **6b** | 30-day rolling window | Days 1–30 included; **day-31 read/write drops day-1 at request time** — no sweeper dependency |
| **7** | | `.c` regression — evidence class | Same I-S-13 pair as .c #6/#7 — label **regression**, not novel coverage |
| **8** | | Prior suites + verifier orthogonality | Literal: `samvaad_acceptance` **22**, `samvaad_b_acceptance` **14**, `samvaad_c_acceptance` **18** → **54**; verifier emit sites unchanged |

### Row vs test arithmetic

| Surface | Count |
|---------|-------|
| Matrix rows | **8** |
| `samvaad_d_acceptance` tests | **11** (rows **1**, **4**, **6** = two tests each) |
| SAMVAAD rollup after `.d` | **54 + 11 = 65** |
| `i_acceptance` | **Separate** — not in 65 |

**Marker:** `samvaad_d_acceptance`

### Compose assertion (row #8 — literal string)

```text
samvaad_acceptance: 22
samvaad_b_acceptance: 14
samvaad_c_acceptance: 18
→ compose: 54
```

---

## Named exclusions

| Item | Disposition |
|------|-------------|
| Dhaara event on INSERT | **SAMVAAD.e** — see [`samvaad_e.md`](./samvaad_e.md) |
| Postgres 021 summative projector hardening | **SAMVAAD.e** |
| I-S-13 summative **lift** | Trigger doc + Framework v2 pin — not `.d` |
| Phase 2 marketing instrumentation | Excluded — baseline-first |
| New matcher kinds / new `evidence_class` | Excluded — Q2/Q3 |

---

## Gate

1. ~~Scope fork~~ **Resolved** — `.e` holds Dhaara/Postgres draft  
2. ~~I-S-13 trigger doc~~ [`I-S-13_lift_trigger.md`](../deferred/I-S-13_lift_trigger.md) **created**  
3. ~~CF-1 storage~~ **022 formative table (shape a)** pinned  

**Countersigned and closed (2026-08-24):** `samvaad_d_acceptance` **11/11**;
prior slices **54/54** unchanged; SAMVAAD rollup **65/65**. `i_acceptance`
remains separate. **SAMVAAD.e** is next for scope walk.

- **Arithmetic:** `22 (.a) + 14 (.b) + 18 (.c) + 11 (.d) = 65`.
- **CF-1 (a):** 022 is formative-authoritative; 021 remains
  summative-authoritative; positive class filters guard summative reads.
- **CF-2:** verifier emit-site count remains exactly one
  (`samvaad/verify_service.py`); `verifier_emit.py` is unchanged.
- **Deferred:** I-S-13 lift remains condition-anchored in
  [`I-S-13_lift_trigger.md`](../deferred/I-S-13_lift_trigger.md); Phase 2
  marketing remains baseline-gated.
- **Enabling precursor:** local authoring coherence repair `acbb4a3` unblocked
  repository collection and row #8. Replace with the post-merge `main` SHA
  when a remote merge exists.
