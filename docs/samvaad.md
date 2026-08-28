# NAADI · SAMVAAD

## Human Performance & Communication Competency

**Framework status:** v1 **frozen** (invariant set tightened 2026-08-23) · SAMVAAD.a enforced · reference rubrics v1 · matcher spec v1

> **NAADI proves not only whether a healthcare worker knows what to do clinically, but whether they can communicate, coordinate, use digital systems, regulate themselves under pressure, and operate safely with real patients and teams.**

**Clinical skill gets you hired. Human skill keeps the patient safe.**

**Architectural spine (stated once, at the top):** Pratibimb tests the clinical decision. SAMVAAD tests how the human delivers that decision. Downstream scope decisions reference this sentence — same discipline as F closeout's trust-ladder framing.

**What Samvaad is.** A first-class competency system inside NAADI — not soft-skills training. Same primitives as clinical skill: positive-evidence rubrics (Nirikshak), competency nodes with competency-specific decay (Dhaara), Error-DNA vector extension, Drishti workplace observation, Pratibimb Proof eligibility. A rubric hit either fired or it did not. A verifier can reproduce the decision.

---

## Where Samvaad Sits Inside NAADI

```
                         NAADI
                          │
              HEALTHCARE WORKFORCE TRUST
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
   PRATIBIMB           SAMVAAD           DRISHTI
 Clinical competence   Human competence   Observation
 simulation            & communication    evidence
        │                 │                 │
        └──────────────┬──┴─────────────────┘
                       ▼
                   NIRIKSHAK
               Evidence evaluation
                       │
                       ▼
                EVIDENCE LEDGER
                       │
                       ▼
               PRATIBIMB PROOF
              Reproducible evidence
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
        DHAARA        GURU         BEEMA
     competence      remediation   patterns/
       state                       decay/safety
           │
           ▼
        NIYUKTI
   hiring / deployment
           │
           ▼
    HOSPITAL OUTCOMES
```

---

## Why This Matters

Two nurses both correctly identify sepsis. Nurse A fails to escalate clearly, uses jargon with family, incomplete handoff, mistypes patient ID, does not challenge an incorrect instruction, disorganizes under pressure. Nurse B escalates with SBAR, literacy-matched language, two-source ID verify, appropriate challenge, accurate handoff.

Traditional exam: both may "know sepsis." Real hospital: **they are not equally safe.** Samvaad is another dimension of clinical competence — not fluff. That is the pitch that lands with a clinical director in ninety seconds, and it converts SAMVAAD from a nice-to-have into a patient-safety-adjacent workstream that survives budget cuts.

---

## Provenance Note (A–F Reconstruction Gap)

**Status:** Domains A–F body text is **reconstructed v1, pending source recovery** — derived from review of the **GPT** synthesis draft (not Deepseek), not recovered verbatim from an authored source.

Review-signed specifics that must survive any future diff-merge:

| Pin | Must remain verbatim / shape-faithful |
|-----|---------------------------------------|
| Team Communication / hierarchy | "steep hierarchy — junior staff hesitate to question seniors" |
| Empathy rubric id | `comm.empathy.acknowledge_distress` (multi-signal AND combinator; cue phrases supporting-only) |
| SBAR | `fail_case_on_violation: true` on incomplete handoff |
| Code-switching | appropriateness / register-match axis, not language detection |
| Positive-evidence YAML | matcher-based; no `attitude: 7/10` subjective scales |

**When source recovery lands — reconciliation shape (b):** verbatim wins as A–F prose SoT; reconstruction v1 archived as `reconstruction_v1_archive`; per-item reconciliation notes. Reconstruction does not linger as an alternate SoT.

---

## Domain Set (v1) · Scoping Target

**v1 ships nine domains (A–I).** Domain *set* is versioned; additions are additive-only; removals require v2 migration. **Ordinal-stable:** letters A–I keep fixed meaning across framework versions; v2 extends with J, K, … — never reorder or rename A–I. A rubric authored against v1 domain C remains domain C under v2. (Invariant I-S-1.)

**Scoping estimate (not an invariant):** ~40–55 micro-competencies across A–I in v1. This is a design-doc target for corpus sizing, not a system property a verifier checks.

| Domain | What it covers | Why it matters in India |
|---|---|---|
| **A. Patient Communication** | Plain-language explanation, teach-back, literacy-matched register, code-switching as appropriateness | Register mismatch — not language detection — is the load-bearing measurement. |
| **B. Empathy & Attunement** | Multi-signal interaction behavior (pause ∧ acknowledge ∧ jargon reduction ∧ teach-back); veto on interruption after acknowledge | Single-cue string matches overfit to scripts. Combinator AND — not independent OR. |
| **C. Informed Explanation & Consent** | Procedure purpose, risks in vernacular, voluntary consent signals, refusal handling | Low-literacy consent theatre is a known ward failure mode. |
| **D. Team Communication & Handoff** | SBAR completeness, closed-loop orders, shift transfer discipline | Incomplete handoffs kill patients. Critical misses, not lost points. |
| **E. Speaking Up Across Hierarchy** | Challenge wrong orders, escalate without shame, graded assertiveness | Steep hierarchy — junior staff hesitate to question seniors — is among the strongest India-specific SAMVAAD components. |
| **F. Family & Conflict Navigation** | Family spokesperson dynamics, bad-news framing, de-escalation without capitulation | Decisions often run through family clusters. |
| **G. Professional Presentation** | **Ward readiness** — ID badge, uniform/PPE, infection-control presentation, punctuality | Never score attractiveness, accent, skin tone, polish, class-coded mannerisms. Bias review before Phase 3. |
| **H. Digital Literacy (Basic)** | Patient-ID verification, HIS/EMR, specimen scanning, credential hygiene, shared-terminal logout | Clinical safety competency — right test → wrong patient. |
| **I. Stress Management & Self-Regulation** | Pause under load, request support, known reset, protocol maintenance, recovery after error | **Observable behavior only** for credentialing. Biometrics = formative self-awareness only — never credential / hiring / BEEMA. |

---

## Evidence Architecture (tightened)

### Nirikshak is the single evaluation surface

Matchers extend the Nirikshak rubric DSL. No parallel SAMVAAD-specific scoring engine.

### Empathy — multi-signal AND combinator

Signals are **combined** to fire a hit; they are not scored independently as OR.

```
patient distress cue
       ↓
pause ∧ acknowledge ∧ jargon_reduction ∧ teach_back
       ↓
empathy rubric hit
```

**Veto:** acknowledge then interrupt twice within 30s → miss (even if pause/acknowledge fired). Phrase cues (`supporting_cue_phrases`) are never sufficient alone. No voice-tone / prosody / sentiment as credential signals.

### Preceptor vs machine evidence classes

| `evidence_class` | Meaning | Dhaara weight (v1) |
|------------------|---------|---------------------|
| `machine_sim` | Nirikshak rubric hits from sealed sim transcript | base confidence |
| `preceptor_attested` | Human-attested ward observation (Drishti) | higher class weight |
| `patient_reported` | Phase 4 only — gated out of summative until I-S-13 lifts | n/a until Phase 4 |

Pinned enum: `{machine_sim, preceptor_attested, patient_reported}`. Class-aware confidence weighting in Dhaara. See open-question A (answered below).

### Observer-bias response path (not theatre)

When bias signals exceed threshold (systematic lower scores by gender / language / center):

1. **Down-weight** that preceptor's attestations in Dhaara (class weight × bias factor).
2. **Review** — flag to supervisor / quality lead (auditable event).
3. **Retrain** — mandatory observer-calibration module before new attestations accepted.
4. **Remove** — revoke attest authority if remediation fails (versioned, auditable).

Response path is versioned (`bias_remediation_workflow.v1`). **Allowed transitions:**

```text
active → down_weighted → under_review → retraining → active
                                      └→ removed
                         retraining ──→ removed
removed (terminal)
```

Monitoring without a defined response is not an invariant.

### SAMVAAD summative ↔ H digest discipline

Summative SAMVAAD evidence is captured against a **sealed transcript** with the same digest discipline as clinical grading: `transcript_digest` + three-version tuple (`grader_version`, `rubric_schema_version`, `artifact_schema_version`). H invariants apply. RCP replay eligibility (I-S-15) binds to the G/H portable-artifact contract — not standing alone as intent.

### Patient feedback — Phase 4

Out of evidence chain until consent / coercion-resistance / literacy-appropriate instrument ships.

---

## Reference Rubrics

One worked rubric per domain A–I — versioned as `samvaad.reference_rubrics.v1.1` (+ content hash). Combinator AND fields added at Framework invariant tighten (not a silent in-place v1 edit). Further changes ⇒ v1.2 / v2.

Safety-critical `fail_case_on_violation` allowlist (pinned per version): `comm.handoff.sbar_complete`, `digi.patient_id.two_source_verify`, `digi.terminal.logout_on_shift_end`, `team.hierarchy.safety_challenge`.

---

## Rollout

| Phase | Domains | Rationale |
|---|---|---|
| **Phase 1** | A, B (formative → summative after matcher), **H** | Digital literacy = immediate safety events. |
| **Phase 2** | D, E, **I** | Stress enables hierarchy / emotional-regulation work. |
| **Phase 3** | C, F, **G** | Presentation after bias review. |
| **Phase 4** | Patient-reported interaction evidence | After consent/governance. |

**Cadence (honest):** Design target 2–3 h/week additional is optimistic. With preceptor debriefs and peer feedback, expect closer to **4–5 h/week** for working AHP students. **Phase 1 pilot must instrument actual cohort time-on-task** and recalibrate any marketing number before Phase 2. Do not ship "manageable even for working students" until that study.

Pilot inversion: `PHASE1_PILOT_IMMINENT = False` until a calendar date is published here; "soon" does not invert.

---

## Commercial Positioning

**Before:** "We verify clinical competence."

**After:** "We verify whether a healthcare worker can perform safely as a complete professional — not just whether they know the clinical answer."

**Claim hygiene:** no observed wage-premium numbers; mechanism language only.

---

## Invariants Index (I-S-1…15) — Framework v1 Freeze

Enforcement = named test, structural constraint, or explicit `policy, no test`. Scoping estimates are **not** invariants.

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **I-S-1** | **Domain set versioned; additive-only; ordinal-stable.** v1 = A–I with fixed ordinals; v2 may append J, K, …; **never** reorder, rename, or remove A–I. Domain C in v1 == domain C in v2 for I-S-14 transcript interpretability | `DOMAIN_ORDINALS_V1` tuple + `assert_domain_ordinal_stable()`; `next_additive_domain_letter()`; test `test_samvaad_a_17_*` |
| **I-S-2** | **Positive observable evidence only.** No attitude / personality / inferred-state scoring in summative credentialing | Schema deny-list + CI grep (`attitude`, `professionalism_score`, `empathy_score`, `personality_*`, …) |
| **I-S-3** | **Nirikshak rubric DSL is the single evaluation surface.** Matchers extend the DSL; no parallel SAMVAAD scoring engine | Import-graph: no `samvaad.scoring_engine`; SAMVAAD matchers register into Nirikshak matcher registry path |
| **I-S-4** | **`fail_case_on_violation` only for clinically justified competencies.** Allowlist pinned per version | `FAIL_CASE_ALLOWLIST`; belt: fail-case competencies ⊆ allowlist |
| **I-S-5** | **Code-switching = appropriateness signal** vs patient literacy/language state — not raw language detection | Domain A: `register_appropriateness`; forbid `language_detected` as sole pass |
| **I-S-6** | **Empathy matchers are multi-signal AND combinators** with **interruption veto** within pinned window from empathy-cue onset | `combinator: multi_signal_and`; `EMPATHY_CUE_WINDOW_MS=5000`; `window_ms_from_empathy_cue_onset`; test `test_samvaad_a_18_*` |
| **I-S-7** | **Dhaara freshness and decay are competency-specific.** No universal recertification cycle | Per-node decay class; soft-skill ≠ technical. Recomputation trigger pinned (Q-B below) |
| **I-S-8** | **Error-DNA carries observable-pattern axes only.** No mood / personality / disposition axes | Feature-registry deny-list + grep belt |
| **I-S-9** | **No biometric data in credentialing, hiring, or BEEMA risk decisions.** Formative-only, isolated | Schema patterns + credentials/beema grep + no `samvaad.biometric_summative` import-graph |
| **I-S-10** | **Preceptor-attested and machine-generated evidence in distinct classes;** Dhaara weighting class-aware | `EvidenceClass` enum `{machine_sim, preceptor_attested, patient_reported}`; schema requires `evidence_class` on ledger evidence rows when SAMVAAD writes |
| **I-S-11** | **Preceptor observations versioned, auditable, bias-monitored.** Remediation state machine: `active → down_weighted → under_review → retraining|removed`; `retraining → active|removed`; `removed` terminal | `bias_remediation.py` `ALLOWED_TRANSITIONS`; Drishti GA tests on synthetic bias fixture |
| **I-S-12** | **Presentation rubrics forbidden from aesthetic/class/caste proxies.** Ward-readiness only | Domain G `forbidden_features` compile gate |
| **I-S-13** | **`patient_reported` summative fail-closed.** Formative capture allowed. Summative issuance/credentialing refuses. Lift = Phase 4 Framework pin only | `assert_summative_evidence_class`; .c: HTTP 422 + `error_kind=summative_evidence_class_forbidden` + `samvaad_summative_rejected_total{reason="patient_reported"}`. Subjective patient_feedback* keys still schema-denied in summative payloads |
| **I-S-14** | **SAMVAAD summative evidence captured against sealed transcripts** with same digest discipline as clinical grading; H invariants apply | Summative write path requires `transcript_digest` + `(grader_version, rubric_schema_version, artifact_schema_version)` — lands with summative capture slice; stub asserts field names in `samvaad.summative_contract` |
| **I-S-15** | **All summative SAMVAAD evidence eligible for RCP replay** under G/H portable-artifact contract | No ephemeral-only summative scores; ledger-addressable artifacts; inherits H portable-envelope discipline |

**Dropped from prior draft (not invariants):** "40–55 micro-competencies" (scoping estimate only); "Adaptive practice, standardized summative" (undefined slogan — revisit when Guru/IRT selection is designed); standalone domain *count* freeze (replaced by I-S-1 set-versioning).

**Prior SAMVAAD.a test IDs** (`test_samvaad_a_*`) still enforce I-S-2/9/13 structural absences and reference rubrics; Framework freeze renumbered the invariant index — see mapping in [`design/samvaad_a.md`](./design/samvaad_a.md).

---

## Open Questions — Answered at Freeze

### A. Evidence-class tag on preceptor observations

**Pinned enum values** for ledger / evidence rows:

```text
evidence_class ∈ { machine_sim, preceptor_attested, patient_reported }
```

| Value | Writer | Summative eligible (v1) |
|-------|--------|-------------------------|
| `machine_sim` | Nirikshak / Pratibimb sim grade | yes, if sealed transcript |
| `preceptor_attested` | Drishti | yes |
| `patient_reported` | Phase 4 instrument | **no** until I-S-13 lifts |

Dhaara stores `evidence_class` + class-aware confidence weight. Schema drift without this enum is forbidden — values live in `services/pratibimb/samvaad/evidence_class.py`.

### B. Dhaara node freshness recomputation trigger

**Both — event-primary, cron-secondary:**

1. **Event-based (primary):** new evidence → immediate freshness recompute for that node (and dependents per Dhaara edge rules).
2. **Cron (secondary):** daily decay sweep at **02:00 UTC** — not local hospital TZ. Ops: [`ops/samvaad_dhaara_freshness.md`](./ops/samvaad_dhaara_freshness.md).

Pin: `DHAARA_FRESHNESS_TRIGGER = event_on_evidence + daily_decay_cron`. Cron never invents evidence — decay only.

Summative **fail-closed:** `assert_summative_evidence_class("patient_reported")` raises under I-S-13 — enforced in `test_samvaad_a_19_*`.

Citation anchor: `framework_acceptance_fixture()` exports `framework_citation_anchor` — grep-visible in test artifacts (`test_samvaad_a_20_*`).

---

## SAMVAAD.a — Closed

See [`design/samvaad_a.md`](./design/samvaad_a.md). Migration **018** (`samvaad_verifier`). Acceptance: `pytest -m samvaad_acceptance` → **22/22**.

**Citation:** *Framework v1 — SAMVAAD.a enforced; reference rubrics v1.1; matcher spec v1; invariants I-S-1…15 frozen.*

---

## Milestone Plan

**SAMVAAD.a** — Closed (enforcement stubs, empathy matcher spec, nine-rubric dry-runs, `samvaad_verifier`).

**SAMVAAD.b** — **Closed** — corpus, empathy runtime stubs, Digital Literacy dry-run, summative capture, `samvaad_verifier` emit stub. [`design/samvaad_b.md`](./design/samvaad_b.md). `pytest -m samvaad_b_acceptance` → **14/14**.

**SAMVAAD.c** — **Fully countersigned** — Nirikshak registry merge, migration **021**, summative verify route, always-on emit. [`design/samvaad_c.md`](./design/samvaad_c.md). `pytest -m samvaad_c_acceptance` → **18/18** · SAMVAAD compose **54/54**. Ship: [`ops/samvaad_c_ship_checklist.md`](./ops/samvaad_c_ship_checklist.md).

**SAMVAAD.d** — **Closed** — matcher params, I-S-13 capture (022), observability-only time-on-task, runtime bias detector. [`design/samvaad_d.md`](./design/samvaad_d.md). `pytest -m samvaad_d_acceptance` → **11/11** · prior compose **54/54** · SAMVAAD compose **65/65**.

**SAMVAAD.e** — **Closed** — Postgres 021/022 projectors, migration **023**
tenant-scoped dedup, deterministic Dhaara event/reconciliation path, and
bounded Prometheus metrics. [`design/samvaad_e.md`](./design/samvaad_e.md).
`pytest -m samvaad_e_acceptance` → **14/14** · prior compose **65/65** ·
SAMVAAD compose **79/79**.

**v0.1 — Phase 1** — A, B, H + cohort time-on-task instrumentation.

**v0.2 — Phase 2** — D, E, I.

**v0.3 — Phase 3** — C, F, G post bias review; bias remediation workflow live.

**v0.4 — Phase 4** — Patient-reported; I-S-13 lift by explicit pin only.

---

## The One-Line Positioning

*Clinical skill + human performance + evidence + hospital validation.*

> **Clinical skill gets you hired. Human skill keeps the patient safe.**
