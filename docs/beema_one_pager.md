# NAADI · BEEMA

## Clinical Safety Intelligence for the Healthcare Workforce

**The problem.** Indian hospitals hire allied-health workers on paper credentials that don't predict bedside safety. Skills decay silently between recertifications. Near-misses go uncounted. Insurers price clinical risk without observing clinical behavior. Regulators audit facilities, not competence trajectories. The result: preventable adverse events that everyone in the value chain pays for, and nobody can see coming.

**What BEEMA is.** BEEMA is the safety intelligence layer of NAADI. It reads from a versioned evidence ledger — typed simulation traces, rubric-scored performance, preceptor-confirmed observations — and produces auditable signals about where clinical risk is accumulating, why, and what intervention reduces it. It is infrastructure for risk reduction, not a score for risk labeling.

## Four Primitives

### 1. Performance Pattern Vector

A multi-dimensional feature set derived per case from typed trace events: decision latency, escalation hesitation, anchoring on early differentials, premature closure, vernacular-language friction, recovery velocity after adverse flags. Computed deterministically from evidence. Never inferred from biometrics in v0.1.

Internally: `error_dna`. Externally: Performance Pattern Vector.

### 2. Skill Decay Model

Competency-specific decay curves calibrated per clinician role, learned from longitudinal re-assessment data. Converts recertification from calendar-based ("annual refresher") to evidence-based ("your dialysis technicians' airway-management competency has decayed 18% since their last observed procedure — schedule micro-simulation this month"). This is the wedge that opens hospital data-sharing agreements.

### 3. Counterfactual Autopsy Engine

For any adverse case outcome in simulation, replays the deterministic physiology engine along alternative decision branches to quantify *modeled* consequence deltas. Outputs carry provenance bands (`scope: in-simulation`, `physio_version`, `scenario_version`) — never presented as real-world mortality predictions until independently validated against outcome data.

### 4. Clinical Passport

A worker-owned, cryptographically verifiable evidence profile — not a single score. Portable across employers. Shows capability vectors with confidence intervals, evidence freshness, and preceptor-confirmed observation rates. The clinician controls disclosure.

## How BEEMA Is Different

BEEMA does not own judgment. It composes views over an append-only evidence ledger:

```
Typed Trace → Rubric Evidence → Evidence Ledger →
Performance Pattern Features → Longitudinal Safety Signals →
Intervention Recommendation
```

Every signal is traceable to the underlying events, the rubric version that scored them, the physiology version that generated them, and the preceptor (if any) who confirmed them. If a hospital, insurer, or regulator asks *"why does this signal say what it says?"* — BEEMA answers with the evidence chain, not with a model card disclaimer.

## Who Buys It

**Hospitals** pay for continuous competency surveillance, evidence-based recertification triggers, onboarding assessment, and unit-level safety dashboards (e.g. "night-shift airway competency by ward").

**Insurers** pay for aggregate portfolio analytics at the facility, unit, or district level — never individual clinician risk labels. K-anonymity floor of 50, differential-privacy noise on small-cohort queries, enforced at the query layer.

**Governments and public health missions** pay for district- and state-level workforce readiness maps: where are the shortages of verified ICU technicians, which skills are decaying in rural PHCs, which training investments moved the needle.

## What BEEMA Does NOT Do (v0.1)

- No individual clinician monetary risk pricing.
- No employment or hiring decision inputs.
- No insurance premium inputs at the individual level.
- No biometric-derived scoring. Opt-in biometric telemetry is collected as research data only, in a separate storage domain that does not touch the ledger.
- No re-identification of aggregate analytics. Enforced structurally, not by policy.

These are not concessions. They are the reason a hospital DPO, an IRDAI-adjacent counsel, or an NCVET auditor will sign the data-sharing agreement.

## The Moat

Not the feature vector — anyone can invent features. The moat is the longitudinal linkage: typed trace + rubric evidence + versioned physiology + preceptor-confirmed observation + eventual real-world outcome data, all sharing one canonical event grammar from simulation into supervised clinical practice. Every additional partner hospital compounds the calibration. Every additional preceptor-confirmed case tightens the signal. That linkage is what competitors — global or domestic — cannot replicate without India-native hospital partnerships and years of longitudinal collection.

## Milestone Plan

**v0.1 (Q4 2026).** Performance Pattern Vector shipped on simulation-only evidence. Skill Decay model calibrated on internal recertification cohort. Counterfactual Autopsy scoped to in-sim provenance. Passport format spec published, no external claims of predictive validity.

**v0.2 (Q2 2027).** Preceptor-confirmed workplace observation integrated (Drishti Shadow Mode). Skill Decay validated against real re-assessment outcomes. Aggregate hospital dashboard GA.

**v0.3 (2028).** Longitudinal outcome linkage with pilot hospital partners. Predictive validity study published. Insurer aggregate analytics product opens.

## The One-Line Positioning

*NAADI is designed to connect clinical simulation, longitudinal competency evidence, supervised workplace validation, and workforce outcomes into one India-native safety intelligence layer.*
