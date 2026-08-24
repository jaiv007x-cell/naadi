# SAMVAAD.a — Enforcement Stubs + Matcher Spec + `samvaad_verifier`

**Parent:** [`samvaad.md`](../samvaad.md) Framework v1  
**Review pattern:** pins → invariants → matrix-as-acceptance-spec → code against green matrix  
**Status:** **closed** — matrix 16/16 green (2026-08-23)

## Boundary sentence

SAMVAAD.a lands structural enforcement for Framework v1 invariants that must be testable before Phase 1 content ships: subjective-scale denial (I-S-3), biometric absence from summative/credential/BEEMA surfaces (I-S-10), patient-feedback absence from summative namespace (I-S-13), immutable reference rubrics v1, empathy interaction-behavior matcher *spec*, and closed-enum `caller_kind=samvaad_verifier` via migration **018**. It does not open a parallel audit table, does not implement full Nirikshak pause/jargon detectors, and does not lift Phase 4 patient feedback.

## Pre-diff pins (frozen)

| # | Pin | Value |
|---|-----|-------|
| 1 | I-S-10 / I-S-13 | Named schema patterns + grep belts + import-graph (see `samvaad/invariants.py`) |
| 2 | Reconstruction | Shape **(b)** — archive + per-item notes when source recovers |
| 3 | Reference rubrics | `samvaad.reference_rubrics.v1` immutable; edit ⇒ v2 |
| 4 | Pilot inversion | `PHASE1_PILOT_IMMINENT = False` (no date pinned 2026-08-23) |
| 5 | Migration | **018** after 017 |
| 6 | Matrix | 16 cases · `pytest -m samvaad_acceptance` |

## Deliverables

| Artifact | Path |
|----------|------|
| Deny-lists + validators | `services/pratibimb/samvaad/invariants.py` |
| Empathy matcher spec | `services/pratibimb/samvaad/matcher_spec.py` |
| Reference rubrics v1 | `services/pratibimb/samvaad/reference/v1.py` |
| Fail-case allowlist | `services/pratibimb/samvaad/fail_case_allowlist.py` |
| Schema validate / dry-run | `services/pratibimb/samvaad/schema.py` |
| `caller_kind` | `audit/caller_kinds.py` + migration `018` + model CHECK |
| Migration checklist | `docs/migrations/018_samvaad_verifier_checklist.md` |
| Acceptance suite | `tests/test_samvaad_a.py` |

## Acceptance matrix (20)

| # | Case | Invariant / pin |
|---|------|-----------------|
| 1–16 | *(prior SAMVAAD.a cases)* | see below |
| 17 | Domain ordinals A–I stable; v2 extends J | I-S-1 ordinal-stable |
| 18 | Empathy veto window `EMPATHY_CUE_WINDOW_MS=5000` | I-S-6 |
| 19 | `evidence_class=patient_reported` summative fail-closed | I-S-13 |
| 20 | Citation anchor in `framework_acceptance_fixture()` | cite grep-visible |

**Run:** `pytest -m samvaad_acceptance` → **20/20**

## Explicit non-goals

Full matcher runtime in Nirikshak · Phase 1 pilot content beyond reference shapes · Dhaara wiring · Drishti GA · parallel audit table · biometric formative dashboard implementation · wage-premium claims · Deepseek diff-merge (amendment when source arrives)

## Gate

**SAMVAAD.a closed 2026-08-23** — `pytest -m samvaad_acceptance` **20/20** (carry-forward cases 17–20 landed same day).

**Framework v1 invariant tighten (same day):** I-S-1…15 (dropped scoping estimate; domain-set versioning; empathy AND combinator; evidence_class; H digest binding; bias remediation path). Reference rubrics bumped to **v1.1**. Citation: *Framework v1 — SAMVAAD.a enforced; reference rubrics v1.1; matcher spec v1; invariants I-S-1…15 frozen*.

### Invariant ID mapping (SAMVAAD.a tests → Framework freeze)

| SAMVAAD.a / old | Framework v1 freeze |
|-----------------|---------------------|
| I-S-3 subjective | **I-S-2** |
| I-S-6 fail-case | **I-S-4** |
| I-S-7 code-switch | **I-S-5** |
| empathy steps | **I-S-6** (AND combinator) |
| I-S-10 biometric | **I-S-9** |
| I-S-13 patient feedback | **I-S-13** (unchanged) |
| I-S-14 presentation | **I-S-12** |
| (new) evidence class | **I-S-10** |
| (new) sealed transcript | **I-S-14** |
| (new) RCP / H bind | **I-S-15** |

Diff walked before SAMVAAD.b opens.
