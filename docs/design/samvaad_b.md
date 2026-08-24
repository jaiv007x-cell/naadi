# SAMVAAD.b — Rubric Surface + Summative Capture

**Parent:** [`samvaad.md`](../samvaad.md) Framework v1 (I-S-1…15 frozen)  
**Predecessor:** SAMVAAD.a **closed** — `pytest -m samvaad_acceptance` **22/22**  
**Status:** **closed** — `pytest -m samvaad_b_acceptance` **14/14** (2026-08-23)  
**Review pattern:** matrix-as-acceptance-spec → code against green matrix → diff walked before SAMVAAD.c

## Boundary sentence

SAMVAAD.b opens the nine-domain rubric corpus, empathy AND-combinator runtime stubs, Digital Literacy Phase 1 dry-run, summative capture with I-S-14 fields, and `samvaad_verifier` emit stub on the shared audit surface. `patient_reported` remains fail-closed for summative. No parallel audit table.

## Framework stop rule (PR description pin)

> **If this slice touches Framework-layer structure** (AND-gate composition, interruption-veto *existence*, `DOMAIN_ORDINALS_V1` reorder/remove/rename, I-S-* invariant text) → **stop and re-countersign**. Rubric-param tunes (`EMPATHY_CUE_WINDOW_MS`, step weights, cue phrases) stay in-slice under rubric versioning only.

## Seven questions — pinned answers

| # | Answer |
|---|--------|
| 1 Corpus storage | In-package `SamvaadCorpus` over reference rubrics v1.1 (authoring DB deferred) |
| 2 Matcher registration | `samvaad.matcher_kinds.SAMVAAD_MATCHER_KINDS` in .b; Nirikshak registry merge in .c when detectors land |
| 3 Summative write | `samvaad.summative.capture_summative` projector (in-memory .b; ledger wiring later) |
| 4 Pilot date | `PHASE1_PILOT_IMMINENT=False` — empathy-first holds |
| 5 evidence_class on write | **Required** on every summative row from day one |
| 6 Domain J | Out of scope |
| 7 Regression | `samvaad_acceptance` 22/22 + `samvaad_b_acceptance` 14/14 compose |

## I-S-11 ladder (named exclusions)

```text
active → down_weighted → under_review → retraining → active
                                      └→ removed          (sole pool-exit path)
                         retraining → under_review        (fail/refuse; NOT removed)

NAMED EXCLUSION: no resolved / cleared state
ILLEGAL: retraining→removed, down_weighted→removed, active→removed, removed→*
```

## Acceptance matrix (14) — green

| # | Case | Status |
|---|------|--------|
| 1 | Corpus nine domains / ordinals | ✓ |
| 2 | Ordinals immutable | ✓ |
| 3 | Empathy runtime AND + window | ✓ |
| 4 | Rubric↔matcher window parity | ✓ |
| 5 | Digital Literacy H dry-run | ✓ |
| 6 | Summative missing fields reject | ✓ |
| 7 | patient_reported reject | ✓ |
| 8 | machine_sim + preceptor accept | ✓ |
| 9 | Bias legal / illegal (incl. retraining→removed) | ✓ |
| 10 | RCP-shaped fields | ✓ |
| 11 | No parallel audit | ✓ |
| 12 | samvaad_verifier emit | ✓ |
| 13 | Citation fixture | ✓ |
| 14 | Framework vs rubric layer split | ✓ |

## Deliverables

| Artifact | Path |
|----------|------|
| Corpus | `samvaad/corpus.py` |
| Runtime | `samvaad/runtime.py` |
| Summative | `samvaad/summative.py` |
| Verifier emit | `samvaad/verifier_emit.py` |
| Versioning split | `samvaad/versioning.py` |
| Matcher kinds | `samvaad/matcher_kinds.py` |
| Tests | `tests/test_samvaad_b.py` |

## Non-goals (unchanged)

Full 40–55 corpus · Drishti live · Phase 4 patient feedback · Phase 3 presentation · Dhaara cron job · Nirikshak registry merge · Domain J

## Carry-forwards closed into .c gate

| # | Pin | Landed |
|---|-----|--------|
| 1 | `error_kind=illegal_transition` on `BiasRemediationError` | yes |
| 2 | Zero production `emit_samvaad_verifier_audit(` sites (.b tree) | yes — `test_samvaad_b_12` |

**Next:** [`samvaad_d.md`](./samvaad_d.md) — scope draft for Dhaara event path + Postgres 021 projector.
