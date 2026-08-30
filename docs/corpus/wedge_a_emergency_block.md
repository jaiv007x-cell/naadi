# Wedge A — Emergency / First-Aid Block (authoring gate)

**Status:** Countersigned **2026-08-30** — repo and memo must agree; any new design doc
is held against this list.  
**Parent:** [`review_ui.md`](../design/review_ui.md) · signature primitive · superintendent-auditable ledger  
**Out of scope:** BEEMA, Gulf Q-banks, new product lobes wearing new names.

## Commercial frame (milestone language)

| Concept | Pin |
|---------|-----|
| **Pilot (the house)** | ₹2.5 lakh prices the **eight-case pack** running in a college lab with a superintendent-auditable ledger |
| **Signature (the door key)** | A **signed Ramesh** is **milestone one** of that pilot — not the invoice line |
| **Licence pack** | Cases **#9–20** (19 firm + 1 optional after **#18 dropped**) — opens only after pilot **#8** carries `clinical_reviewer` + `reviewed_at` |

Nothing in this document couples to the SAMVAAD three-PR merge chain.

---

## Case list — 19 firm + 1 optional (#18 dropped)

**Canonicalization (2026-08-30):** No verbatim pre-reconstruction table exists on either
side. The spine table below was built from Aug 28 case specs plus countersigned pins.
**The committed table in this file is the canonical table** — authoritative as of
commit **`e0f1fde`**. If a pre-reconstruction table ever surfaces, diff it against
this doc; on conflict **this doc wins** (repo-and-memo-must-agree runs repo → memo).

**Shape:** every case ships **3 required (fail-closed spine) + 6 supporting** rubric hits — the loop validated on Ramesh transfers to the whole pack without rubric-architecture rework.

**Criterion polarity in spine column:** `R:` required · `F:` forbidden (absence scored) · `O:` ordered (sequence)

| # | Pack | Case ID | Persona · presentation | Setting | Fail-closed spine (3 required) | Sign order |
|---|------|---------|------------------------|---------|--------------------------------|------------|
| **1** | Pilot | `ramesh_kale.inferior_stemi.v1` | Ramesh Kale, 58M — inferior STEMI / RV preload | District ED, Maharashtra | **R:** inferior STEMI on 12-lead ECG · **O:** V4R / right-sided leads before nitrate · **R:** fluid bolus on RV hypotension | 1 |
| **2** | Pilot | `meena_kulkarni.anaphylaxis.v1` | Meena, 24F — ceftriaxone anaphylaxis | OPD / PHC, Maharashtra | **R:** IM adrenaline first line · **F:** no IV adrenaline bolus (conscious) · **F:** no suspected allergen re-given | 2 |
| **3** | Pilot | `sunita_devi.pph.v1` | Sunita Devi, 34F — day-2 PPH, shock | Rural PHC, Bihar | **R:** hemorrhagic shock recognized · **R:** uterotonic within window · **R:** escalation to CHC / blood availability | 3 |
| **4** | Pilot | `sunita_devi.severe_pet.v1` | Sunita Devi, 32F — severe pre-eclampsia | PHC → referral, Bihar | **R:** severe features (BP / proteinuria) · **R:** MgSO₄ loading · **F:** no discharge home | 4 |
| **5** | Pilot | `arjun_reddy.dengue_critical.v1` | Arjun Reddy, 8M — dengue critical phase | Corporate / district ER | **R:** critical-phase transition (afebrile-but-worse) · **R:** weight-based ml/kg fluids · **F:** don't send home | 5 |
| **6** | Pilot | `baby_aarav.neonatal_sepsis.eos.v1` | Baby Aarav, 3d M — EOS sepsis (proxy: Meera) | Private nursing home, Chennai | **R:** danger-sign cluster (feeding, RR, CRT, fontanelle) · **R:** empiric abx + thermal care · **F:** no 20 ml/kg bolus (neonatal flood trap) | 6 |
| **7** | Pilot | `rajesh_singh.op_poisoning.v1` | Rajesh Singh, 42M — organophosphate (field spray) | Rural ER, Punjab | **R:** atropine + pralidoxime per protocol · **R:** ABC + decontamination · **F:** no milk/oil folklore | 7 |
| **8** | Pilot | `priya_nair.snakebite.v1` | Priya Nair, 28F — Russell's viper, WBCT20 failed | Rural paddy / CHC, Kerala | **R:** WBCT20 at 20 min · **R:** ASV 10 vials initial (non weight-based) · **F:** no fasciotomy / incision / suction | 8 |
| **9** | Licence | `fatima_sheikh.dka.v1` | Fatima Sheikh, 52F — Ramadan-break DKA | Govt tertiary, Delhi | **O:** fluids before insulin · **R:** potassium checked and repleted · **R:** fasting-safe regimen counseling | — |
| **10** | Licence | `mohammed_iqbal.stroke.v1` | Mohammed Iqbal, 62M — wake-up stroke, NIHSS 14 | Srinagar winter / houseboat | **R:** NIHSS documented · **F:** don't discharge confused · **R:** thrombolysis / referral tree | — |
| **11** | Licence | `kavya_sharma.paed_dehydration.v1` | Kavya Sharma, 2F — gastroenteritis dehydration | District hospital | **R:** dehydration grade assessed · **R:** ORS / IV plan by weight · **F:** no bolus without assessment | — |
| **12** | Licence | `amit_verma.burns.v1` | Amit Verma, 26M — flame burns, TBSA | Casualty, urban | **R:** TBSA / fluid resuscitation plan · **R:** cooling + airway · **F:** no butter/oil on burn | — |
| **13** | Licence | `lakshmi_iyer.choking.v1` | Lakshmi Iyer, 70F — FB airway obstruction | Home → ambulance | **R:** complete vs partial obstruction · **R:** back blows / abdominal thrusts · **F:** no blind finger sweep | — |
| **14** | Licence | `suresh_patel.hypoglycemia.v1` | Suresh Patel, 55M — diabetic hypoglycemia | OPD / ward | **R:** bedside glucose · **R:** dextrose / glucagon · **F:** no discharge without meal plan | — |
| **15** | Licence | `ram_kishan.heat_stroke.v1` | Ram Kishan, 48M — exertional heat stroke | Construction site → PHC | **R:** heat stroke vs exhaustion · **R:** active cooling + IV fluids · **F:** no antipyretic-only management | — |
| **16** | Licence | `ravi_kumar.mdrtb.v1` | Ravi Kumar, 45M — cavitary TB, prior incomplete ATT | Urban slum clinic, Mumbai | **R:** MDR risk stratified · **R:** NTEP / GeneXpert referral · **R:** contact-tracing scope | — |
| **17** | Licence | `baby_no_name.neonatal_resus.v1` | Term newborn — birth asphyxia / apnoea at delivery | Labour room, tier-2 | **R:** initial steps (warm, dry, stimulate) · **R:** PPV if apnoeic · **O:** compressions only after PPV failure | — |
| **18** | — | **DROPPED** | *Duplicate of #17 neonatal resuscitation lane (EOS sepsis resus overlap)* | — | — | — |
| **19** | Licence | `arjun_reddy.asthma.v1` | Arjun Reddy, 6M — acute severe asthma | Paediatric ER | **R:** severity assessed (SpO₂ / speech) · **R:** salbutamol + spacer · **F:** no sedation as first line | — |
| **20** | **Optional** | `community.rabies_pep.v1` | Dog-bite PEP — category III exposure | PHC / casualty | **R:** wound wash 15 min · **R:** PEP schedule (RIG + vaccine) · **F:** no primary suturing before wash | — |

**Pilot envelope note:** cases **#1–8** are adult maternal vitals or adult envelope space except **#6** (proxy-informant neonatal sepsis uses draft envelope; full non-adult vitals parameterization is a **licence cost**, not a pilot blocker).

**Licence gate:** case **#9** does not open for authoring until pilot **#8** is signed.

---

## Engineering observations (authoring workload — not scope reopen)

### 1. Fail-closed spine = 3-required rubric layer

Every pilot row already carries its required triad in the table above. Same structure as Ramesh's existing fail-case trio (`content/ramesh-stemi-draft/README.md`). The authoring loop under debug on Ramesh **is** the loop for the whole pack.

### 2. Rubric criterion classes — schema check (2026-08-30)

Three classes cover the spine column without a new lobe:

| Class | Nirikshak surface | Example in pack |
|-------|-------------------|-----------------|
| **Required** | `order_placed`, `drug_given`, `finding_recognized`, … + `required=true`, `fail_case_on_violation=true` | Ramesh ECG recognized |
| **Forbidden** | `drug_not_given` + `fail_case_on_violation=true` (absence as safe practice) | Anaphylaxis IV adrenaline withheld; allergen not repeated |
| **Ordered** | `sequence` matcher (`steps`, `max_gap_s`) | DKA fluids → insulin; neonatal resus PPV before compressions |

**Verified against:** `services/pratibimb/app/eval/nirikshak.py` matchers `drug_not_given`, `sequence`.  
**Residual gap (#7–#8 authoring only — not Ramesh walk):** order-level forbidden
actions (`discharge_home`, `fasciotomy`) need symmetric coverage. **Prefer
`order_not_placed`** over flag-based equivalents — keeps Required/Forbidden/Ordered
uniform across drug-level and order-level forbidden actions. Flag-based works but
leaks case-specific logic into rubric plumbing. SAMVAAD dialogue
`min_occurrences_per_turn` is a separate layer.

### 3. Physio envelope — licence cost flag

Cases **#11** (paediatric dehydration) and **#17** (neonatal resuscitation) need **non-adult vitals parameterization** — neonatal HR/RR/SpO₂ baselines are not adult ranges scaled down. `envelope-v1` was authored against Ramesh. Pilot **#1–8** stays within adult / maternal envelope space. Cost lands when **#9** opens (gated on 1–8 signatures).

---

## Signature sequencing

| Rule | Detail |
|------|--------|
| **Sign order** | Pilot signatures land **1 → 8** sequentially |
| **Draft pipelining** | Case #2 persona/envelope may dry-run while #1 is in clinical review (anaphylaxis rubric already exists) |
| **Licence open** | `clinical_reviewer` + `reviewed_at` on **#8** before **#9** opens — not lockstep authoring |
| **Metric** | Days-from-draft-to-signature stays honest; pipelined drafting prevents serialized-wait inflation |

---

## Drift enforcement (CI — not reviewer vigilance)

`eval/` **must not** import from `persona/`, `tenant/`, or any frontend path. Enforced by import-boundary test in CI (rides review-UI PR per [`review_ui.md`](../design/review_ui.md)); fails the build, not the reviewer's attention span.

Manifest hash construction, append-only signature rows, forbidden-words test — unchanged, pinned in `review_ui.md`. Forbidden-word list is **not** extended speculatively here.

---

## Syllabus mapping

See [`syllabus_mapping.md`](./syllabus_mapping.md). College name is a **sales-relationship fact** — fallback baseline is INC revised GNM emergency/first-aid units until a named college document exists. Tick-marked syllabus version becomes the sold pack; crossed-out units leave the authoring queue.

---

## Standing tests

1. **Repo ↔ memo:** this file is the gate; new corpus proposals cite row numbers or fail review.
2. **No tenth lobe:** BEEMA, Gulf Q-banks, and unnamed product expansions are out of Wedge A.
3. **Milestone one:** signed Ramesh (`80e29c6e…`) — review screen walk continues; cardiologist date is the human gate ([Ramesh README](../../content/ramesh-stemi-draft/README.md)).
