# I-S-13 — Summative lift trigger for `patient_reported` (deferred)

**Condition-anchored only — no calendar fallback.** Summative eligibility lift requires explicit **Framework v2 countersign** after all AND conditions below are met. Absence of partners/events does **not** auto-promote on a date. Premature summative promotion is strictly worse than delayed promotion.

**Parent invariant:** I-S-13 in [`samvaad.md`](../samvaad.md) — `patient_reported` allowed at formative capture; summative fail-closed until this trigger fires + Framework pin.

---

## Lift conditions (all required)

| # | Condition | Verification |
|---|-----------|--------------|
| 1 | **Three independent hospital partners** onboarded for structured patient-reported capture | Named partner IDs in ops record; not single-site duplication |
| 2 | **≥ 200 events** collected under structured instrument (post-procedure / teach-back survey class) | Count from `samvaad_formative_evidence` (022) with required `source_context` + `submitted_by` |
| 3 | **≥ 0.80 inter-rater reliability** on coded empathy/communication items across partner sites | External analysis artifact linked in Framework pin PR |
| 4 | **External clinical reviewer signoff** — attestation that evidence quality supports summative use | Signed review doc referenced in Framework pin PR |

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
