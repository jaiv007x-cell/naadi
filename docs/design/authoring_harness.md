# Authoring Harness — Design Spec

**Status:** Signed off (2026-08-20) — tightenings from design review incorporated  
**Audience:** NCVET clinical reviewers, case authors, platform engineers  
**Last updated:** 2026-08-20

> **The harness is a producer of frozen case artifacts, not a consumer of evidence.**  
> That boundary is load-bearing: it keeps scope from bleeding into read-side dashboards, learner views, and BEEMA integration six months from now.

---

## 1. What “authoring” means (scope boundary)

The harness is a **producer of frozen case artifacts**, not a consumer of evidence.

| In scope | Out of scope |
|---|---|
| **`CaseBlueprintV2` skeleton** — identity, clinical truth, physiology spec, interaction constraints, targeting | Read-side dashboards (BEEMA, ledger read API, cohort analytics) |
| **`GradingBlueprint` authoring** — rubric hits, matchers, weights, pass thresholds | Learner-facing runtime UI (simulation session, dialogue) |
| **Matcher DSL → `RubricHit` compilation** with validation against shared registries | Retroactive in-place edits after publish |
| **Dry-run grading** against recorded `PhysioTrace` fixtures | Bulk export to regulators (summative read path, gated elsewhere) |
| **Review / approval workflow** with provenance stamps | Session management, physio engine tuning |

**Single versioned artifact:** case skeleton and grading blueprint are **not** authored independently and joined at publish. One `{case_id, version}` tuple, one `compute_content_hash()`, one approval chain. Independent authoring produces drift — a rubric referencing an action ID the case no longer emits, or a case graded against a rubric nobody approved.

- **Case authoring** edits `_HASHED_SECTIONS` (clinical substance).
- **Rubric authoring** edits `grading_blueprint` (also hashed — a rubric change is a clinical-meaning change).
- The harness workspace may edit both; persistence always writes **one** immutable artifact.

The harness does **not** replace offline tooling (`migrate_cases_v1_to_v2`, seed corpus). It is the **interactive, validated path** for creating and promoting `CaseBlueprintV2` instances. Legacy imports enter at `DRAFT` and follow the full workflow (§8).

---

## 2. Immutability contract

### 2.1 Freeze on publish (primary invariant)

**A blueprint becomes immutable at `PUBLISHED`, not at first ledger reference.**

| Operation | Allowed after `PUBLISHED`? | Mechanism |
|---|---|---|
| Edit in place | **No** | Harness refuses writes when `workflow_state == PUBLISHED` |
| Unpublish and edit | **No** | Publish is one-way; fixes require a new version |
| “Save as new version” | **Yes** | Bump `identity.version`, recompute hash, new store row; prior version stays queryable |
| Query old version | **Yes** | Append-only by `(case_id, version)`; ledger retains `case_version` + `replay_hash` |
| Delete version | **No** | Retirement = `RETIRED` + `probe_only=True`, never DELETE |

**Rationale:** Freeze-on-publish is simpler to defend than freeze-on-first-reference. A published blueprint with zero sessions is still a committed clinical artifact; allowing “unpublish and tweak” creates unaudited edits. The cost of a typo fix is one version bump — cheap.

**Belt-and-suspenders:** even pre-publish, harness refuses in-place edits once a **finalized ledger row** references `(case_id, version)` (`confirmation='confirmed'`). Ledger reference is a hard stop; publish is the normative freeze for approved artifacts.

Historical grades remain reproducible: a session graded against `anaphylaxis_v3` in Q2 resolves to the exact bytes active at completion, regardless of `v4`/`v5`.

### 2.2 Hash semantics (reuse — do not fork)

From `CaseBlueprintV2.compute_content_hash()`:

- Hashes: `identity`, `targeting`, `patient`, `clinical_truth`, `environment`, `physiology`, `interaction`, `grading_blueprint`, `schema_version`.
- **Excludes** `provenance` and workflow metadata.
- Harness **must** call `with_content_hash()` before submit-for-review; UI displays computed hash and rejects drift.

### 2.3 Grading blueprint isolation

`GradingBlueprint.canonical_dict()` feeds `CaseBlueprintV2.content_hash` and Nirikshak `evaluation_manifest_hash`. Harness publishes blueprints **only** through the case object. `rubric_version` is informational; **canonical hash is authoritative**.

---

## 3. Review / approval workflow

### 3.1 States

```
DRAFT ──submit──▶ IN_REVIEW ──approve──▶ APPROVED ──publish──▶ PUBLISHED ──retire──▶ RETIRED
  ▲                    │
  └──── revert ────────┘
```

Summative GRANTs accumulate while `IN_REVIEW` (append-only approval events); they are **not** a state edge to `PUBLISHED`. Publish is always `APPROVED → PUBLISHED`.

| State | `CorpusTier` | Runnable in sim? | Summative-eligible? | Ledger on completion? |
|---|---|---|---|---|
| `DRAFT` | `draft` | Yes (author tenant) | No | No (dry-run only) |
| `IN_REVIEW` | `draft` | Yes (reviewers) | No | No |
| `APPROVED` | `silver` | Yes | No | Yes (formative/practice) |
| `PUBLISHED` | `gold` (if criteria met) | Yes | GOLD + summative + reviewer stamped | Yes |
| `RETIRED` | unchanged, `probe_only=True` | No new sessions | No | Existing rows unaffected |

Workflow state lives in the persisted envelope — excluded from content hash.

### 3.2 Transitions, scopes, and distinct principals

Reuse JWT + `ConsentGrantStore` — **no parallel role system**.

| Transition | Required consent scope | Notes |
|---|---|---|
| `DRAFT → IN_REVIEW` | `authoring:submit` (tenant-scoped) | Author attests `validation_errors()` empty |
| `IN_REVIEW → APPROVED` | `authoring:approve` | Single approver for formative/practice |
| Summative GRANT (while `IN_REVIEW`) | `authoring:approve_summative` × **two distinct principals** | Append-only; does not change workflow state |
| `APPROVED → PUBLISHED` (formative) | `authoring:approve` | Publisher ≠ author |
| `APPROVED → PUBLISHED` (summative) | `authoring:approve_summative` | Publisher ≠ author and ≠ either summative approver |
| `PUBLISHED → RETIRED` | Same as the corresponding publish action — `authoring:approve` (formative) or `authoring:approve_summative` (summative) | Retirement is content-lifecycle, not system administration; the qualification bar matches the publish bar it reverses. Retirer ≠ publisher. |

**Summative two-approver rule (enforced at state machine, not UI):**

1. Two active summative GRANT events required before summative publish.
2. **`approver_1_subject_id != approver_2_subject_id`** — same principal clicking twice from different sessions is rejected.
3. Both approvers must hold `authoring:approve_summative` for the **case tenant**. This scope is **distinct from** `authoring:approve`; a normal preceptor cannot satisfy the summative path.
4. Author cannot be either approver on summative publish (`author_subject_id ∉ {approver_1, approver_2}`).

Summative publish additionally requires:

- `CaseBlueprintV2.validation_errors()` empty (blueprint, named reviewer, GOLD tier).
- Dry-run suite passes against **versioned golden fixture pair** (§5) — publish-time **reject**, not warning.

Clinical reviewer written to `provenance.clinical_reviewer` at first summative approval (idempotent backfill at publish if still null).

### 3.3 Institutional admin scope (decided)

- Institutional admins approve cases **for their own tenant only**. No cross-tenant approval, no global super-admin scope.
- Cross-tenant sharing uses an explicit **“publish to shared catalog”** transition with its own approval path; consuming tenants **re-approve for local use**.
- `authoring:admin` is tenant-bound via `ConsentGrantStore`, same as all other scopes.
- **`authoring:admin` is not used for case lifecycle transitions** (submit / approve / publish / retire). It remains reserved for tenant/registry-level operations (for example, withdrawing another principal's summative GRANT).

### 3.4 Authoring audit trail

Every transition emits an **authoring audit event** (separate from ledger read audit):

`artifact_type`, `case_id`, `version`, `from_state`, `to_state`, `actor_subject_id`, `tenant_id`, `at_utc`, `content_hash`

---

## 4. Matcher authoring DSL (compiler, not editor)

Authors write intent-level rules; harness **compiles** to `RubricHit` + Nirikshak params. The harness is a compiler with the same validation rigor as the scenario compiler.

### 4.1 Single source of truth — no vendored copies

| Registry | Source module | Rule |
|---|---|---|
| Action IDs | `services/pratibimb/app/action_registry.py` — `KNOWN_ACTION_IDS`, `DEPRECATED_ACTION_IDS`, `resolve()` | **Import directly.** No harness-local copy. Stale snapshot → publish passes, grade fails. |
| Matcher kinds | Extract `KNOWN_MATCHER_KINDS` from Nirikshak's matcher registry (same keys as `Nirikshak._matchers` today; future: shared `eval/matcher_registry.py` imported by both Nirikshak and harness) | **Import directly.** |

### 4.2 No escape hatches

Same discipline as `FlagCause` (no `OTHER`):

- **No** `matcher_kind="custom"` with free-text expressions.
- **No** unregistered action IDs (resolve deprecated → canonical, else compile error).
- **No** unknown matcher kinds — compile error, blueprint cannot reach `IN_REVIEW`.

Example DSL (illustrative):

```yaml
- id: aspirin_given_if_no_contraindication
  axis: action
  matcher: action_within          # must ∈ KNOWN_MATCHER_KINDS
  params:
    action_id: give_aspirin_325_chewed   # must ∈ KNOWN_ACTION_IDS
    within_seconds: 600
  points: 2.0
  required: true
```

### 4.3 Validation gates

| Check | When | Failure |
|---|---|---|
| Action ID registry | Save + submit + publish | Compile error |
| Matcher kind registry | Save + submit + publish | Compile error |
| `points >= 0` | Compile | Error (positive-evidence only) |
| Duplicate `hit.id` | Compile | Error |
| Safety-axis coverage (summative) | Publish | **Reject** |

Compilation calls `GradingBlueprint.canonical_dict()` and Nirikshak entrypoints — **no second grader**.

---

## 5. Dry-run / preview mode

### 5.1 Contract

```
dry_run(blueprint: CaseBlueprintV2, trace: PhysioTrace) → CaseGrade
```

- **Per-request** `AUTHORING_DRY_RUN=1` — not process-wide `LEDGER_DISABLED`. Authors dry-run on the same worker where other sessions write to the ledger normally.
- **No ledger write**, **no session finalize**.
- Production Nirikshak + pinned `PhysioTrace` fixtures.

### 5.2 Golden fixture pair — part of the versioned artifact

For summative cases, the **golden + critical-miss trace pair is stored with the blueprint version**, not as optional metadata:

```json
{
  "fixtures": {
    "golden_pass": { "trace": {...}, "expect": "pass" },
    "golden_critical_miss": { "trace": {...}, "expect": "fail" }
  }
}
```

- Fixture hashes included in publish validation (pinned to `physiology.engine_version`).
- **Missing fixture pair → publish rejected** (summative). Not a warning.
- Dry-run at publish must show: golden pass **passes**, critical miss **fails** on required hits.

Fixtures ship in phase **A** (see §9) — authoring without dry-run is authoring blind.

### 5.3 Preview surfaces

Grade summary, hit matrix, learner-visible subset, `evaluation_manifest_hash`. Output ephemeral unless exported for reviewer sign-off.

---

## 6. Provenance stamp on publish

Provenance envelope (excluded from content hash) captures the full regulator-facing audit trail:

| Field | When set |
|---|---|
| `author_subject_id` | First submit to `IN_REVIEW` |
| `approver_subject_ids` | Each approval (`APPROVED` or summative `PUBLISHED`) |
| `approved_at_utc` | First approval |
| `published_at_utc` | `PUBLISHED` transition |
| `clinical_reviewer` | Summative first approval (also in `Provenance` dataclass) |
| `harness_version` | Every publish — **which tool version produced this artifact** |
| `content_hash` | `compute_content_hash()` at publish |

`Provenance` dataclass fields (`author`, `clinical_reviewer`, `reviewed_at`, etc.) remain the runtime contract; envelope extends with approver list and harness version for NCVET audit queries.

Published corpus row shape:

```json
{
  "workflow_state": "PUBLISHED",
  "published_at": "...",
  "harness_version": "0.1.0",
  "approver_subject_ids": ["...", "..."],
  "fixtures": { "...": "..." },
  "blueprint": { ... }
}
```

---

## 7. Auth integration

| Concern | Approach |
|---|---|
| Identity | `JwtAuthContextProvider` + tenant JWKS |
| Authorization | `ConsentGrantStore` scopes: `authoring:submit`, `authoring:approve`, `authoring:approve_summative`, `authoring:admin` — all tenant-scoped |
| Summative reads | Existing summative middleware (harness is not a regulator client) |

Routes: `/v1/authoring/*` — separate router, same app, same auth stack.

---

## 8. Explicit non-goals

The harness **will not**:

1. Serve learner dashboards or preceptor cohort analytics (BEEMA / ledger read).
2. Edit or delete finalized ledger rows or audit trails.
3. Run live simulations (`SessionManager` + physio worker).
4. Replace Dhaara posterior updates or Skill Decay pipelines.
5. Bypass summative fail-closed grading (`UngradableSessionError` remains runtime law).
6. Store raw learner PII — pseudo IDs only.
7. **Bulk-import from external formats into `PUBLISHED`.** Legacy imports (`migrate_cases_v1_to_v2`, CSV, third-party exports) enter at **`DRAFT` only**, with full DSL re-validation and the same `DRAFT → IN_REVIEW → APPROVED → PUBLISHED` path. No bulk bypass.

---

## 9. Phased delivery

| Phase | Deliverable | Notes |
|---|---|---|
| **A** | Case store schema (`authoring.*`) + `DRAFT`/`IN_REVIEW` state machine + hash display + **dry-run endpoint** + fixture storage | No publish path yet |
| **B** | Matcher DSL compiler + shared registry imports (`KNOWN_ACTION_IDS`, `KNOWN_MATCHER_KINDS`) | Depends on A |
| **C** | Full approval workflow + distinct-principal enforcement + authoring audit table | Depends on B |
| **D** | Summative publish path + golden-fixture gate + `PUBLISHED` immutability | Depends on C |
| **E** | Publish-to-runtime-corpus pipeline (replaces manual JSON edit) | Depends on D |

Dry-run is phase **A**, not deferred — golden-fixture gating and summative publish depend on it.

---

## 10. Resolved decisions (formerly open questions)

| Question | Decision |
|---|---|
| **Case store backing** | Same Postgres instance as ledger, **separate schema `authoring.*`**, separate migration lineage. Transactional publish (blueprint + provenance + audit in one commit). Independent schema evolution without coupling to ledger migrations. Same backup/ops surface. |
| **Assessment mode promotion** | Formative → summative **always** requires a version bump. |
| **Institutional admin scope** | Tenant-scoped only; cross-tenant via shared catalog + local re-approval (§3.3). |
| **Fixture storage** | Co-located with blueprint version in `authoring` schema (JSONB column or side table keyed by `(case_id, version)`). |
| **`/metrics` boot guard** | Deploy checklist only (Appendix A). Two acceptable prod configs; refuse to start otherwise. No warn-and-continue. |

---

## Appendix A — Deploy checklist (metrics)

When Kubernetes / ingress manifests land:

```python
if AUTH_MODE == "production" and METRICS_ENDPOINT_AUTH == "jwt":
    raise MetricsAuthConfigError(
        "/metrics must be on an internal-only listener or "
        "behind scrape-token auth in production; JWT auth "
        "will reject Prometheus scrapers"
    )
```

Acceptable prod configurations — **only these two**:

- (a) `/metrics` on internal bind — JWT middleware does not cover it, or
- (b) `METRICS_SCRAPE_TOKEN` bearer auth on `/metrics` — distinct from ledger JWT.

---

## Appendix B — Relationship to existing modules

| Module | Harness relationship |
|---|---|
| `shared/schemas/case_v2.py` | Canonical case contract — do not fork |
| `shared/schemas/grading.py` | Canonical rubric contract |
| `services/pratibimb/app/eval/nirikshak.py` | Dry-run grader; matcher kind source of truth |
| `services/pratibimb/app/action_registry.py` | Action ID source of truth — import, never copy |
| `scripts/migrate_cases_v1_to_v2.py` | Bulk import → **`DRAFT` only**, full re-validation |
| `services/pratibimb/ledger/writer.py` | Secondary immutability check (ledger reference) |

---

## Appendix C — Runtime trust ladder (Phases E–H)

Phases **E–H** extend the harness into audited runtime reads and the NCVET trust ladder. Each phase doc is the SoT for its closeout; this index is the navigation surface.

| Rung | Phase | Closeout doc | Acceptance suite | One-line scope |
|------|-------|--------------|------------------|----------------|
| — | **E** | [`authoring_harness_phase_e.md`](./authoring_harness_phase_e.md) | E1–E3 suites (see E closeout matrix) | Corpus projection + audited catalog reads |
| **1** | **F** | [`authoring_harness_phase_f.md`](./authoring_harness_phase_f.md) | F.a–F.c (**27** green) | NCVET audit record — projection-only `/v1/ledger/` |
| **2** | **G** | [`authoring_harness_phase_g.md`](./authoring_harness_phase_g.md) | G.a + G.b (**34** green) | Signed credential + status list / offline verify |
| **3** | **H** | [`authoring_harness_phase_h.md`](./authoring_harness_phase_h.md) | **`pytest -m h_acceptance`** (**44** green) | Replayable Competency Proof — recompute-under-same-rubric |
| — | **I** (post-ladder) | [`authoring_harness_phase_i.md`](./authoring_harness_phase_i.md) | **`pytest -m i_acceptance`** (reserved; suite empty until I.a) | Alternate-rubric recompute (ARP) — against H boundary; **not** rung 4 |

**Rung 3 closed by Phase H (2026-08-23).** Ops checklist: [`h_phase_closeout_checklist.md`](../ops/h_phase_closeout_checklist.md). Observability runbook: [`credentials_regrade_observability.md`](../ops/credentials_regrade_observability.md).

**Phase I** opens the H named exclusion *score-under-different-rubric* as a post-ladder product surface — see [`authoring_harness_phase_i.md`](./authoring_harness_phase_i.md). It does **not** add a fourth trust-ladder rung.

Ground rules frozen at F closeout (G opening), H closeout (post-H work), and I prelude live in the respective phase docs — not redefined here.

---

*Spec frozen. Phase A (case store + DRAFT/IN_REVIEW + dry-run) may proceed.*
