# SAMVAAD.e — Dhaara Event Path + Ledger Postgres Projector (Scope draft)

**Parent:** [`samvaad.md`](../samvaad.md) Framework v1  
**Predecessor:** SAMVAAD.d **closed** (matcher params · I-S-13 capture scaffolding · time-on-task · runtime bias detector)  
**Status:** **Scope draft** — opens after SAMVAAD.d countersign  
**Framework stop rule:** inherited unchanged — no .e exceptions.

## Boundary sentence

SAMVAAD.e wires evidence landing to **Dhaara event-primary freshness** (I-S-7), hardens the **Postgres ledger projector** for migration **021** (summative) and **022** (formative, from .d), and exports SAMVAAD metrics to Prometheus. Closes .c carry-forwards on production audit sink wiring where the verify path is touched.

**Fork note:** This content was the original `samvaad_d.md` draft; moved here when `.d` was repointed to matcher/capture/runtime scope (2026-08-24).

---

## Scope walk — surfaces touched

| Surface | Prior state | .e intent |
|---------|-------------|-----------|
| `samvaad/ledger_projector.py` | In-memory summative store | Postgres INSERT → `samvaad_summative_evidence` (021) |
| `samvaad/formative_projector.py` (.d) | Migration 022 formative table | Postgres INSERT → `samvaad_formative_evidence` (022) |
| `samvaad/verify_service.py` | In-memory ledger on live write | Route → SQL projectors; tenant-scoped INSERT |
| Dhaara freshness | Constants + ops runbook | **Event path** on INSERT — **both** summative-eligible (021) **and** gated `patient_reported` (022) |
| `samvaad/metrics.py` | In-process counter | Prometheus export |
| Audit emit | In-memory + optional sink | Production sink parity (**CF-2** from .c) |

### Orthogonality (explicit)

- Does **not** touch ARP surface or authoring harness (`i_acceptance` separate).
- Does **not** modify matcher params, `illegal_transition_detector`, or `time_on_task` (.d landings).

### Invariants at stake

- **I-S-7(B):** event path only; cron remains ops/TBD ([`samvaad_dhaara_freshness.md`](../ops/samvaad_dhaara_freshness.md))
- **I-S-10 / I-S-14:** sealed tuple on Postgres INSERT (021 + 022)
- **I-S-13:** summative fail-closed unchanged; 022 rows never enter summative read path (.d regression)
- **021 / 022 INSERT-only:** no UPDATE; duplicate digest → `illegal_transition`
- **Pin 2 (.c):** one production Call; flag gates INSERT not invocation

---

## Q-sharpening (pinned for .e)

| # | Pin |
|---|-----|
| **Q1** | Dhaara event granularity: **per competency hit** (`SkillFreshnessSnapshot` unit) |
| **Q2** | Postgres wiring: **reuse** `ledger/db` `SessionLocal` |
| **Q3** | Dhaara fires on **both** 021 summative-eligible INSERT and 022 `patient_reported` INSERT |
| **Q4** | Decay cron: **no** — event path only |
| **Q5** | Drishti live ingest: **no** |
| **Q6** | Matrix row count: TBD at .e scope walk (draft **8** rows — see below) |

---

## Proposed matrix (draft — freeze at .e countersign)

| # | Case | Asserts |
|---|------|---------|
| 1 | Postgres INSERT 021 | Summative row; I-S-14 fields |
| 2 | Postgres INSERT 022 | Formative `patient_reported` row (.d projector) |
| 3 | Projector idempotency | Duplicate digest → `illegal_transition` on each table |
| 4 | Dhaara event on 021 INSERT | Freshness event after summative INSERT |
| 5 | Dhaara event on 022 INSERT | Freshness event after formative INSERT (gated class) |
| 6 | Class-aware weight | `evidence_class` in freshness payload |
| 7 | Metrics Prometheus | `samvaad_summative_rejected_total{reason=…}` exported |
| 8 | Compose | `.d` + prior **65** SAMVAAD green |

**Marker:** `samvaad_e_acceptance`  
**Rollup:** SAMVAAD-only — **65 + .e** (`i_acceptance` separate)

---

## Gate

Opens after SAMVAAD.d countersign + green matrix.
