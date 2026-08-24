# SAMVAAD.e — Dhaara Event Path + Ledger Postgres Projector (Scope draft)

**Parent:** [`samvaad.md`](../samvaad.md) Framework v1  
**Predecessor:** SAMVAAD.d **closed** (matcher params · I-S-13 capture scaffolding · time-on-task · runtime bias detector)  
**Status:** **Frozen and countersigned** — code walk open against 11/14/79 matrix
**Framework stop rule:** inherited unchanged — no .e exceptions.

## Boundary sentence

SAMVAAD.e wires evidence landing to **Dhaara event-primary freshness** (I-S-7),
hardens the **Postgres ledger projectors** for migration **021** (summative) and
**022** (formative), adds deterministic rebuild/reconciliation, and exports
bounded-cardinality SAMVAAD metrics to Prometheus. It changes the storage
backend beneath `.d` capture without changing `.d` routing semantics.

**Fork note:** This content was the original `samvaad_d.md` draft; moved here when `.d` was repointed to matcher/capture/runtime scope (2026-08-24).

---

## Scope walk — surfaces touched

| Surface | Prior state | .e intent |
|---------|-------------|-----------|
| `samvaad/ledger_projector.py` | In-memory summative store | Postgres INSERT → `samvaad_summative_evidence` (021) |
| `samvaad/formative_projector.py` (.d) | Migration 022 formative table | Postgres INSERT → `samvaad_formative_evidence` (022) |
| `samvaad/verify_service.py` | In-memory ledger on live write | Preserve `assessment_kind` dispatch; swap only the projector backend |
| Dhaara freshness | Constants + ops runbook | Post-commit event path for successful 021/022 INSERTs; reconciliation heals missed publishes |
| `samvaad/metrics.py` | In-process counter | Prometheus export |
| Audit emit | In-memory + optional sink | Production sink parity (**CF-2** from .c) |
| migration **023** | No tenant-scoped DB dedup across both evidence tables | Harden 021/022 with tenant-scoped deterministic digest keys |

### Orthogonality (explicit)

- Does **not** touch ARP surface or authoring harness (`i_acceptance` separate).
- Does **not** modify matcher params, `illegal_transition_detector`, or `time_on_task` (.d landings).
- Does **not** make Dhaara authoritative for I-S-13 event counting.
- Does **not** add an outbox, distributed transaction, Drishti ingest, or decay cron.

### Invariants at stake

- **I-S-7(B):** event path + reconciliation only; decay-cron implementation remains excluded and the existing 02:00 UTC ops contract is unchanged ([`samvaad_dhaara_freshness.md`](../ops/samvaad_dhaara_freshness.md))
- **I-S-10 / I-S-14:** sealed tuple on Postgres INSERT (021 + 022)
- **I-S-13:** summative fail-closed unchanged; 022 rows never enter summative read path (.d regression)
- **021 / 022 INSERT-only:** no UPDATE; duplicate digest → `illegal_transition`
- **Pin 2 (.c):** one production Call; flag gates INSERT not invocation
- **Authority:** 021/022 are evidence SoT; Dhaara is a rebuildable freshness projection.
- **Routing:** `assessment_kind` selects 021/022; `evidence_class` selects confidence weight.

---

## Q-sharpening (resolved for .e)

| # | Pin |
|---|-----|
| **Q1** | Dhaara granularity is **one `SkillFreshnessSnapshot` per competency hit**. Additive schema extension adds optional `evidence_class` + `evidence_weight`; SAMVAAD builders require both. Event envelope remains `type="freshness.snapshot"`; no endpoint-version bump. |
| **Q2** | Reuse `ledger/db` session factory through injection. Migration **023** adds learner/session provenance to 021, adds `tenant_id`, `learner_pseudo_id`, `competency_hits_json`, and deterministic `evidence_digest` to 022, then enforces tenant-scoped UNIQUE keys. Existing rows use the fail-closed backfill procedure below. |
| **Q3** | `assessment_kind` dispatches to 021/022. After commit, both paths publish the same snapshot schema. 021/022 remain authoritative; Dhaara is audit/freshness projection. Reconciliation converges to **exact match**. |
| **Q4** | No decay cron or outbox in `.e`. Post-commit publish is fail-soft; scheduled/startup reconciliation heals misses (default 60s, clamp 15–600s, healthy bound ≤2 intervals; ops runbook). Deterministic rebuild uses the named source IDs/timestamps/digests/order below. |
| **Q5** | No Drishti ingest and no capture-policy change. `verify_service.py` may switch projectors only; `.d` formative/summative results and the exactly-one verifier-audit Call site remain unchanged. |
| **Q6** | Freeze target: **11 rows / 14 tests**; split rows **3** (3a/3b) and **9** (9a/9b/9c). Rollup: **65 + 14 = 79** SAMVAAD-only; `i_acceptance` remains separate. |

### Evidence-class weight map (pinned)

| `evidence_class` | Weight |
|------------------|--------|
| `machine_sim` | **1.00** |
| `preceptor_attested` | **1.35** |
| `patient_reported` | **0.00** until the I-S-13 Framework lift |

The implementation imports `CLASS_CONFIDENCE_WEIGHT`; it does not duplicate
these values inside the projector. Weight changes remain Framework-visible.

### Event boundary and authority

Evidence INSERT and Dhaara publish cannot share one transaction: Dhaara is an
external HTTP/stream service. The pinned sequence is:

1. INSERT and commit the authoritative 021/022 row.
2. Publish the deterministic freshness snapshot post-commit.
3. On publish failure, keep the evidence commit and mark projection stale.
4. Reconcile from 021/022 through the same snapshot builder until exact
   convergence; a second pass is `skipped`, not a duplicate publish.

I-S-13 condition #2 remains an exact count over authoritative 022 rows. Dhaara
is the audit-visible cross-check. Reconciliation requires equality at
convergence; transient post-commit skew blocks lift evidence from being marked
reconciled but does not transfer authority to the stream.

022 is authoritative for existence. Dhaara absence is never evidence absence;
it means “not yet projected” until reconciliation says otherwise.

### Migration 023 shape and backfill

022 does **not** contain `tenant_id` before `.e`. Neither 021 nor 022 currently
stores every field needed to build `SkillFreshnessSnapshot`. Migration 023 is
therefore staged:

1. Add nullable `learner_pseudo_id` + `session_anchor` to 021.
2. Add nullable `tenant_id`, `learner_pseudo_id`, `competency_hits_json`, and
   `evidence_digest` to 022.
3. If either table is non-empty, require an operator-reviewed staging manifest
   keyed by `evidence_id` containing the missing tenant/learner/session/hit
   values. Do not infer tenant or learner from `submitted_by`,
   `source_context_json`, or transcript text. The manifest lists every source
   `evidence_id` and target tenant explicitly;
   `role:samvaad-ledger-oncall-lead` countersigns it before execution.
4. Populate 022 `evidence_digest` from the canonical tuple after backfill.
5. Run explicit pre-DDL assertions for every added required column, including
   `SELECT count(*) FROM samvaad_formative_evidence WHERE tenant_id IS NULL`.
   Every count must be zero. Any nonzero result halts deployment and returns
   the manifest for re-review; no NULL-to-default coercion is permitted.
6. Only after those assertions pass, set columns `NOT NULL`.
7. Add DB constraints with tenant as the leading column:
   - 021 `UNIQUE (tenant_id, transcript_digest)`
   - 022 `UNIQUE (tenant_id, evidence_digest)`

An empty pre-.e table makes the backfill step a no-op. Rollback is app-first and
forward-only once .e rows exist.

### Deterministic rebuild discipline

- **Source ID:** persisted 021/022 `evidence_id`. Capture may generate it once;
  rebuild never regenerates it.
- **Timestamp:** persisted `captured_at_utc`; snapshot `computed_at` uses that
  value, never projection-time `NOW()`.
- **Learner/session:** persisted `learner_pseudo_id`; `source_session_ids` is
  the one-element tuple containing persisted `session_anchor`.
- 021 dedup is tenant-scoped by transcript digest.
- 022 `evidence_digest` is SHA-256 over canonical
  `(learner_pseudo_id, evidence_class, source_context, submitted_by,
  session_anchor, matcher_parameters, competency_hits)`.
- The 022 digest is **content-plus-provenance**, deliberately excluding
  `captured_at_utc`: identical content in different learner/session provenance
  does not collide, while an exact retry in the same provenance scope does.
- **Digest:** snapshot `source_replay_hash` is the persisted
  `transcript_digest` (021) or `evidence_digest` (022).
- **Order:** competency hits retain sealed list order inside one row; cross-row
  rebuild output uses `ORDER BY captured_at_utc ASC, evidence_id ASC`, then
  `competency_id ASC` within each row. Evidence ID is the same-timestamp
  tiebreaker. Row 9 asserts byte-identical ordering across two rebuilds.
- Rebuild never generates projection-time timestamps or sequence IDs.

The additive freshness fields are `evidence_class` and `evidence_weight` on
`SkillFreshnessSnapshot`, not freshness columns on 022. Capture time remains
the existing authoritative `captured_at_utc`. `SkillFreshnessSnapshot` is a
rebuildable projection materialized from both 021 and 022; it is not an
authoritative evidence table.

### Metrics label bounds

Allowed labels are closed enums only:

- `assessment_kind ∈ {summative, formative}`
- `evidence_class ∈ {machine_sim, preceptor_attested, patient_reported}`
- `outcome ∈ {success, skipped, error}`
- rejection `reason ∈ {patient_reported}`

Unknown labels raise `ValueError`. Tenant IDs, session IDs, evidence digests,
transcript digests, submitters, and competency IDs are forbidden as labels.

Exact series:

- `samvaad_evidence_insert_total{assessment_kind,evidence_class,outcome}`
- `samvaad_dhaara_publish_total{assessment_kind,evidence_class,outcome}`
- `samvaad_dhaara_reconcile_total{assessment_kind,outcome}`
- `samvaad_dhaara_reconcile_last_success_timestamp_seconds{assessment_kind}`
- `samvaad_dhaara_projection_stale_seconds{assessment_kind}`
- `samvaad_summative_rejected_total{reason}`

---

## Carry-forward disposition

| # | Carry-forward | Disposition |
|---|---------------|-------------|
| **CF-1** | Dual-path routing | `assessment_kind` routes; row `evidence_class` weights. Table identity is not a substitute discriminator. |
| **CF-2** | Verifier orthogonality | Expected verifier-audit Call-site count remains **`== 1`** at `verify_service.py`. Dhaara calls live inside projector/facade code and do not alter this AST count. |
| **CF-3** | I-S-13 count authority | 022 remains authoritative; Dhaara exact-convergence reconciliation is the audit belt. Trigger doc authority does not change. |
| **CF-4** | Rebuild | Source-row-driven, byte-deterministic snapshot construction; post-commit misses heal through the same builder. |

---

## Acceptance matrix (freeze target)

**Disagreement line:** *11 semantic rows / 14 tests — two rows split: row 3
spans independent 021/022 duplicate constraints as 3a/3b (+1 test), and row 9
spans deterministic 021 materialization, 022 materialization, and Dhaara
rebuild as 9a/9b/9c (+2 tests). All remaining rows are one test each. Three
additional tests come from two split rows, not three split rows.*

| # | Sub | Case | Asserts |
|---|-----|------|---------|
| **1** | | Postgres INSERT 021 | Fixed summative fixture lands tenant-scoped I-S-14 row with `assessment_kind='summative'` |
| **2** | | Postgres INSERT 022 | Distinct fixed formative fixture lands full provenance + competency hits with `assessment_kind='formative'`; no 021 row |
| **3** | **3a** | Duplicate 021 digest | Duplicate `(tenant_id, transcript_digest)` → shared `BiasRemediationError(error_kind=illegal_transition)` |
| | **3b** | Duplicate 022 digest | Duplicate `(tenant_id, evidence_digest)` → the same `illegal_transition` kind |
| **4** | | Dhaara event after 021 commit | One uniform `freshness.snapshot` per competency hit with summative assessment kind and source evidence class |
| **5** | | Dhaara event after 022 commit | Same event schema with formative assessment kind; competency IDs read from stored `competency_hits_json` |
| **6** | | Class-aware weight | Snapshot carries source `evidence_class`; applied weight equals the pinned map |
| **7** | | Prometheus export | INSERT, publish, reconcile, and rejection series export only closed labels; no high-cardinality labels |
| **8** | | Prior compose | Literal `22 (.a) + 14 (.b) + 18 (.c) + 11 (.d) = 65`; verifier-audit site remains exactly one |
| **9** | **9a** | Deterministic 021 materialization | Same fixed source record produces byte-identical canonical SQL values |
| | **9b** | Deterministic 022 materialization | Capture time does not affect digest; same learner/session + content hashes equal, while different learner or session hashes differ |
| | **9c** | Deterministic Dhaara rebuild | Authoritative rows rebuilt twice produce byte-identical snapshots and `CompetencyState`, ordered by capture time, evidence ID, then competency |
| **10** | | Exact reconciliation | Missed publish is healed; counts converge exactly; second pass skips without duplicate snapshots |
| **11** | | `.d` regression | Actual `.d` formative/summative routing fixtures return unchanged results under SQL backend; CF-2 AST guard remains `== 1` |

**Marker:** `samvaad_e_acceptance`  
**Tests:** **14**
**Rollup:** SAMVAAD-only — **65 + 14 = 79** (`i_acceptance` separate)

### Compose assertion (literal)

```text
samvaad_acceptance: 22
samvaad_b_acceptance: 14
samvaad_c_acceptance: 18
samvaad_d_acceptance: 11
→ prior compose: 65
samvaad_e_acceptance: 14
→ SAMVAAD compose: 79
```

---

## Gate

**Countersigned (2026-08-24):** **11 rows / 14 tests / SAMVAAD 79**;
migration 023 dedup/backfill shape, additive freshness schema, deterministic
rebuild, and post-commit + exact-reconciliation boundary frozen. Code opens
only against this matrix.
