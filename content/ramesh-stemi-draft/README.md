# Ramesh Kale STEMI — draft case fragments

Branch: `content/ramesh-stemi-draft` (off `main`).

**Purpose:** extend the seeded Ramesh Kale inferior-STEMI persona for
engine-validation drafts. Persona + physiology only in this pass.
Dialogue trees and the 9-hit rubric wait until `feat/samvaad-e` lands on
`main`. No leakage into the three-PR merge chain.

## Runtime artifact

The runtime / harness artifact is a single **`CaseBlueprintV2` JSON
document** whose top-level keys match `blueprint_json`:

`identity`, `targeting`, `patient`, `clinical_truth`, `environment`,
`physiology`, `interaction`, `grading_blueprint`, `provenance`,
`schema_version`.

File: [`blueprint_fragment.json`](./blueprint_fragment.json)

There are **no** orphan `persona.yaml` / `physio_envelope.yaml` packs.
If a future pass stages split sources for authoring ergonomics, those
files are staging only — a compile step must emit this single JSON, and
the staging convention must be named here. Until then, edit the JSON
directly.

## This pass — authored sections

| Section | Status |
|---------|--------|
| `patient` | authored (demographics, `mr` language, Roman chief-complaint notes) |
| `physiology` | authored (initial vitals, STEMI deterioration rule strings) |
| `clinical_truth` | authored (needed for STEMI substance; comorbidities via meds + `interaction.hidden_facts`) |
| `identity` / `targeting` / `environment` | minimal scaffold so the fragment rehydrates |
| `interaction.dialogue_constraints` | empty — see TODO |
| `grading_blueprint` | `null` — see TODO |

`corpus_tier` is `draft`. `assessment_mode` is `practice`. No clinical
reviewer. Learner-queue gold publish stays closed.

## Staging convention (explicit)

| Kind | Path | Consumed by runtime? |
|------|------|----------------------|
| Blueprint fragment (JSON) | `content/ramesh-stemi-draft/blueprint_fragment.json` | Yes — target shape for `CaseDraftStore.blueprint_json` |
| This README | `content/ramesh-stemi-draft/README.md` | No — authoring notes only |
| Validate helper | `content/ramesh-stemi-draft/validate_fragment.py` | No — local schema gate |

Do not add YAML siblings unless a compile step is introduced that emits
`blueprint_fragment.json`.

## Language pin

- Native code: `mr` (Marathi). Hindi Roman companion strings live in
  `patient.persona_notes` only.
- Learner-facing complaint phrasing: **Roman transliteration**, matching
  `seed_corpus.json`. No Devanagari in this draft.

## TODO — park until `.e` on `main`

Do not resolve these on this branch:

1. **Dialogue constraints / turn graph** — `interaction.dialogue_constraints`
   stays `[]`. Empathy windows, cue anchors, `min_occurrences_per_turn`,
   and `allow_implicit_cue` bindings belong in a post-`.e` dialogue pass.
2. **9-hit rubric** — `grading_blueprint` stays `null`. Smoke harness
   keeps its 3-hit regression rubric elsewhere; full draft expands to
   3 required + 6 supporting after `.e` merge.
3. **Chief-complaint verbatim map** — V2 has no
   `chief_complaint_verbatim` key. Roman `mr`/`hi` strings are parked in
   `patient.persona_notes` until a schema-backed home exists (or migrate
   reintroduces them). Do not invent a parallel YAML field.
4. **Case #2 (T2DM foot-ulcer, distinct persona)** — opens only after
   this case clears dry-run at 9 rubric hits.

## Validate

```bash
python content/ramesh-stemi-draft/validate_fragment.py
```

Gate: rehydrates via `blueprint_from_case_json`, `validation_errors()`
empty for practice/draft, no `grading_blueprint` hits, no dialogue-turn
or rubric-criterion references in authored string fields.
