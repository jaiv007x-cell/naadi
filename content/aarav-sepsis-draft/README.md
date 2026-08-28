# Baby Aarav — early-onset neonatal sepsis (draft)

Branch: `content/aarav-sepsis-draft` (off `main`).

**Reference case #2** for the Pratibimb library — nursing-forward, proxy-informant
dialogue surface. Persona + physiology only in this pass; 9-hit rubric and turn
graph wait for a post-dialogue pass (same sequencing as Ramesh).

## Structural difference from Ramesh

All history-taking is **proxy history-taking**. The persona engine drives **Meera**
(informant mother), not Aarav. Unlocks pediatrics, geriatric dementia, and
unconscious-patient case classes if dialogue handles this.

## Runtime artifact

File: [`blueprint_fragment.json`](./blueprint_fragment.json) — single `CaseBlueprintV2`
JSON matching `CaseDraftStore.blueprint_json` shape.

| Section | Status |
|---------|--------|
| `patient` | authored (neonate demographics + Meera informant notes) |
| `physiology` | authored (26 deterioration rules, 6 rule families) |
| `clinical_truth` | authored (EOS sepsis; maternal RF in `hidden_facts`) |
| `interaction` | proxy informant pinned; `dialogue_constraints` empty |
| `grading_blueprint` | `null` — post-dialogue pass |

`corpus_tier`: `draft` · `assessment_mode`: `practice` · `targeting.roles`: GNM/ANM

**Content hash (pinned):** `75930a37056f0569de35930cfd875f9cf482aaea6cd6618e113cc3cfa4ccb4ff`

## Deterioration envelope (26 rules)

| Family | Count | Key pedagogy |
|--------|-------|--------------|
| Untreated-progression spine | 6 | **Hypothermia flip** at T+45min reads as "improving" |
| Perfusion decay | 4 | CRT, mottling, pulse pressure, wet-diaper timestamp |
| Neuro | 4 | Lethargy → hypotonia → subtle seizure; fontanelle tense if LP deferred |
| Feeding/metabolic | 3 | Hypoglycemia 38; glucose-without-sepsis = partial credit only |
| Intervention-response | 3 | Amp+gent NNF first-line; 10 ml/kg NS; antipyretic-only fail-path |
| Iatrogenic traps | 4 | 20 ml/kg PALS reflex; gent weight calc; LP while desaturating; warmth neglect |

## Hypothermia misread (declared semantics)

`untreated_temp_flip_hypothermia_after_s=2700:temp_c=36.1` — falling temperature after
initial fever is **progression**, not improvement. Engine and rubric must not score
"stabilizing" on temp alone without sepsis-directed therapy.

## Clinical review gate

Gold-tier review requires **neonatal nurse educator sign-off** on thermal care,
danger-sign cluster scoring, and parental communication hooks — not physician-only
default.

## Rubric hooks (named, unscored this pass)

| Hook | Nursing weight |
|------|----------------|
| Sepsis screen interpretation (TLC, ANC, I:T, CRP) | shared |
| Danger-sign cluster recognition (feeding + RR + CRT + fontanelle) | **primary nursing** |
| Empiric therapy per NNF (weight-based dose math) | shared |
| LP threshold (indicated; defer-not-cancel if unstable) | medical |
| Thermal care (warmer/KMC as intervention) | **primary nursing** |
| Parental communication (gravity without panic; wait-and-watch redirect) | **primary nursing** |
| Proxy-history elicitation (PROM/prophylaxis from uninformed mother) | shared |

## Library queue

| # | Case | Status |
|---|------|--------|
| 1 | Ramesh Kale — inferior STEMI | countersigned on `main` |
| 2 | **Baby Aarav — neonatal sepsis** | this branch |
| 3 | Sunita Devi — PPH (Bihar PHC) | queued |
| 4 | Priya Nair — Russell's viper | spread-check / toxicology contrast |

## Validate

```bash
python content/aarav-sepsis-draft/validate_fragment.py
```
