# Ramesh Kale STEMI — draft case fragments

Branch: `content/ramesh-stemi-draft` (off `main`).

**Purpose:** extend the seeded Ramesh Kale inferior-STEMI persona for
engine-validation drafts. Persona + physiology + post-`.e` dialogue/rubric
scaffold. No leakage into the SAMVAAD three-PR merge chain.

## Runtime artifact

The runtime / harness artifact is a single **`CaseBlueprintV2` JSON
document** whose top-level keys match `blueprint_json`:

`identity`, `targeting`, `patient`, `clinical_truth`, `environment`,
`physiology`, `interaction`, `grading_blueprint`, `provenance`,
`schema_version`.

File: [`blueprint_fragment.json`](./blueprint_fragment.json)

Rubric source pin: `services/pratibimb/authoring/ramesh_draft_rubric.py`
(merged into JSON; no orphan YAML).

## This pass — authored sections

| Section | Status |
|---------|--------|
| `patient` | authored (demographics, `mr` language, Roman chief-complaint notes) |
| `physiology` | authored (28 deterioration rules — inferior STEMI, staged AV block, RV/nitrate) |
| `clinical_truth` | authored (comorbidities via meds + `interaction.hidden_facts`) |
| `identity` / `targeting` / `environment` | minimal scaffold so the fragment rehydrates |
| `interaction.dialogue_constraints` | **authored** (10-branch scaffold) |
| `grading_blueprint` | **authored** (9 hits: 3 required / 6 supporting) |

`corpus_tier` is `draft`. `assessment_mode` is `practice`. No clinical
reviewer. Learner-queue gold publish stays closed.

**Content hash (pinned):** `80e29c6e203589f222c21115f4e85d22187de5524ea72c88b2235cd3f847df57`

## Clinical countersign (fragment walk)

**Verdict: countersigned** after Mobitz II amendment and staged-progression pin.

| Check | Result |
|-------|--------|
| Mobitz II absent | ✓ removed — anterior His-Purkinje pattern forbidden in inferior RCA envelope |
| Staged AV block progression | ✓ brady (T+420) → 1° AV (T+480) → Mobitz I (T+540) → CHB (T+600) |
| Atropine response | ✓ `atropine_response_symptomatic_brady:hr_rise=+15` |
| RV nitrate trap | ✓ steep drop `sbp_drop=-30:within_ticks=3`; stacks with untreated floor |
| RV fluid rescue | ✓ `rv_hypotension_after_fluid_bolus:sbp_recovery=+20` |
| Rule order determinism | ✓ array order is authored evaluation order (stable dry-run pin) |

Prior hash `8277af12…` superseded by amendment (Mobitz II → staged block family).

## Rubric (9 hits)

**Required trio (pedagogical spine):**

1. `stemi.inferior_ecg_recognized` — inferior STEMI on initial ECG
2. `stemi.v4r_before_nitrates` — right-sided leads before nitrates (RV discoverable)
3. `stemi.rv_hypotension_fluid_rescue` — fluid bolus rescue on preload-dependent hypotension

**Supporting six:** aspirin timing, pain reassessment, communication register,
atropine on symptomatic brady, cath lab activation, allergy check.

Smoke harness keeps its separate 3-hit regression rubric in
`services/pratibimb/authoring/ramesh_smoke.py`.

## Hypotension path stacking (declared semantics)

The RV/nitrate trap has two SBP paths that can co-fire:

1. Time-conditioned untreated floor: `rv_hypotension_untreated_after_s=600:sbp_floor=85`
2. Event-conditioned nitrate trap: `rv_hypotension_after_nitrate:sbp_drop=-30:within_ticks=3`

**Declared:** paths **stack relative-to-current with no floor clamp**. Sub-floor
crash is intentional pedagogy for preload-dependent RV infarct — not a bug.

## Language pin

- Native code: `mr` (Marathi). Roman transliteration in `patient.persona_notes`.
- No Devanagari in this draft.

## TODO — still open

1. **Chief-complaint verbatim map** — parked in `persona_notes` until schema home exists.
2. **Case #2 (Baby Aarav neonatal sepsis)** — opens after this case dry-runs at 9 hits.
3. **Floor-stacking engine wire** — README semantics declared; physio-engine enforcement lands with rule execution.
4. **Turn-graph dialogue seeds** — constraint scaffold present; Meera/Aarav dialogue lines are next authoring pass.

## Validate

```bash
python content/ramesh-stemi-draft/validate_fragment.py
```

Gate: rehydrates via `blueprint_from_case_json`, clinical physiology pins,
9-hit rubric compile, dialogue scaffold pin, `content_hash` match.
