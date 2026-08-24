# I-S-13 — Summative lift trigger for `patient_reported` (deferred)

**Condition-anchored only — no calendar fallback.** Summative eligibility lift requires explicit **Framework v2 countersign** after all AND conditions below are met. Absence of partners/events does **not** auto-promote on a date. Premature summative promotion is strictly worse than delayed promotion.

**Parent invariant:** I-S-13 in [`samvaad.md`](../samvaad.md) — `patient_reported` allowed at formative capture; summative fail-closed until this trigger fires + Framework pin.

---

## Lift conditions (all required)

| # | Condition | Why load-bearing | Verification |
|---|-----------|------------------|--------------|
| 1 | **Three independent hospital partners** onboarded for structured patient-reported capture | Cross-site generalization; single-partner reliability does not establish that the matcher transfers | Named partner IDs in ops record; not single-site duplication |
| 2 | **≥ 200 events** collected under structured instrument (post-procedure / teach-back survey class) | Statistical power for a stable reliability estimate | Count from `samvaad_formative_evidence` (022) with required provenance |
| 3 | **≥ 0.80 inter-rater reliability** on coded empathy/communication items across partner sites | Minimum reliability threshold for the proposed evidence use | External analysis artifact linked in Framework pin PR |
| 4 | **External clinical reviewer signoff** — attestation that evidence quality supports summative use | Independent clinical/compliance gate; volume and IRR cannot substitute for it | Signed review doc referenced in Framework pin PR |

The four conditions are an AND gate. Surplus on one axis cannot compensate for a
missing condition on another.

---

## What lift does *not* mean

- Automatic promotion on a calendar date (contrast: rubric-param tunes like `EMPATHY_CUE_WINDOW_MS` may use ops-baseline-or-date review)
- Widening `evidence_class` enum without Framework bump
- Removing formative/summative read-path isolation — only summative **eligibility** for `patient_reported` changes after pin

---

## SAMVAAD.d posture

- **Capture scaffolding** lands in `.d` (migration **022**, formative projector) — collects evidence toward condition #2
- **Summative gate stays closed** until this trigger + Framework countersign
- Lift implementation slice: explicit Framework v2 pin — not bundled into `.d` or `.e`

---

## Owner / review

- **Evidence collection:** SAMVAAD + hospital partner ops
- **IRR analysis:** external clinical methods reviewer
- **Framework lift PR:** requires full Framework stop-rule countersign
