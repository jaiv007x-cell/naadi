# Authoring harness — Phase F (regulator / NCVET audited reads)

Status: **Phase F closed (signed off 2026-08-22).**  
Parent: [`authoring_harness.md`](./authoring_harness.md).  
Prior: [`authoring_harness_phase_e.md`](./authoring_harness_phase_e.md) (**closed**).

**Estimate:** **3–5 weeks** with pins below. +1–2 weeks if date-range queries enter scope; F slips a phase if Q4 expands to a tokenization service.

---

## What Phase F is (and is not)

**Goal:** Ship an **audited, consent-gated HTTP read surface** for regulator-grade consumers to replay graded session evidence and linked case context — distinct from E3 catalog metadata reads and distinct from internal runtime corpus loads (E2.5).

| Surface | Phase | Payload | Audit |
|---------|-------|---------|-------|
| E3 catalog | E closed | Published/retirement **metadata** | `ledger_read_audit` |
| E2 runtime | E closed | Envelope for session/probe (**internal**) | None |
| **Phase F** | This phase | **Graded session evidence + bounded case context** | `ledger_read_audit`, `caller_kind=ncvet_audit` |

### Named exclusions (not “deferred” — **out of Phase F**)

| Exclusion | Rationale |
|-----------|-----------|
| **`regulator_export`** query kind / bulk export surface | Date-range and tenant-wide pulls belong here; building it in F recreates the bulk-export surface under another name |
| **Date-range session queries** (`published_at` / `graded_at` window for tenant) | Same creep path as `regulator_export`; regulators enumerate learners and paginate |
| **Demographics in export shape** | Phase G / separate consent if NCVET mandates equity reporting |
| **NCVET snapshot / quarterly artifact store** | No mandate assumed; revisit only on explicit regulator requirement → **Phase G** |
| **Push / streaming delivery** | Different reliability contract |
| **Mutable export / snapshot table** | Drift + retention + authority problems (Q3/Q5) |

Same discipline as keeping `ncvet_audit` out of E3: **name the exclusion** so scope cannot creep silently.

---

## Seven questions — **pinned**

### Q1 — What exactly is `ncvet_audit` exporting?

**Pinned MVP — “bounded” means query shape, not payload size:**

| Query kind (closed enum) | MVP? | Params | Payload |
|--------------------------|------|--------|---------|
| **`get_session_evidence`** | **Yes (F.a)** | `session_id` | Single graded session evidence bundle + linked case context (envelope slice without demographics) |
| **`list_learner_sessions`** | **Yes (F.b)** | `learner_pseudo_id`, `cursor`, `limit` (cap 100) | Paginated session summaries for one learner |
| **Date-range / tenant-wide session list** | **Out of F** | — | Use learner enumeration + pagination, or **`regulator_export`** (out of F) |

Load-bearing use cases: (a) “show evidence for this graded session”; (b) “show all sessions for this learner” with hard cap + cursor. **Not (c)** “all Q3 sessions for tenant X.”

| Export facet | In MVP? | Source |
|--------------|---------|--------|
| Graded session evidence (trace, rubric, blueprint stamp) | **Yes** | `session_ledger` + read models |
| Case envelope (fixtures, provenance, blueprint **without demographics**) | **Yes, per session** | `runtime.published_case_corpus` via session’s `(tenant_id, case_id, version)` |
| Aggregated rollups / event firehose | **Out of F** | — |

---

### Q2 — Read cadence

**Pinned:** **Pull on demand** — `GET /v1/ledger/...` (JWT + consent scope).

Regulator read pattern is spiky-on-audit, not continuous. Audit trail reuses **`ledger_read_audit`** with `caller_kind=ncvet_audit` — **no second audit surface**.

---

### Q3 — Retention boundary

**Pinned:** **No export table.** Queries hit live **`session_ledger` + `ledger_read_audit` + `runtime.published_case_corpus`** under existing retention. Export surface **is the query API**, not a copy.

Point-in-time (“state as of date X”) = **`WHERE` filter on append-only sources**, not a snapshot store.

---

### Q4 — PII / redaction posture

**Pinned:**

| Rule | Enforcement |
|------|-------------|
| **Pseudo IDs verbatim** | `learner_pseudo_id`, actor pseudo IDs in evidence |
| **Demographics** | **Not exported in F** — never selected at projection layer (not “redacted in handler”) |
| **Free-text name-strip** | **At projection layer** — fields not selected, not filtered post-query |
| **Cross-tenant** | Does not exist — one tenant per token |

**Projection field allowlist (MVP — additions need design review):**

| Include | Exclude |
|---------|---------|
| Session ids, timestamps, rubric scores/outcomes, trace replay fields, blueprint hash, harness/schema version, case_id/version, assessment_mode, fixtures summary, provenance stamp (approver ids, published_at) | **`patient.demographics` entire subtree**, learner-entered free-text notes, faculty comment free-text, any `name` / `verbatim` display fields in envelope |

If NCVET later mandates demographic **reporting**, that is **Phase G** with bucketed aggregates — not individual demographic records in F.

---

### Q5 — Immutability

**Pinned:** `ncvet_audit` reads are a **view**, not a copy. Append-only ledger + audit; **no mutable export table** in F.

Blueprint **`schema_version` in envelope** is the schema record — no long-lived schema snapshot DB (second source of truth).

---

### Q6 — Failure mode

**Pinned:** **Grades land independently; `ncvet_audit` fail-closed 503** (same discipline as E3 `get_retirement_history`).

| Path | Behavior |
|------|----------|
| Grading / ledger append | **No dependency** on `ncvet_audit` availability |
| `ncvet_audit` read | Own gateway + scope check + audit sink; sink failure → **503**, empty body, I-E3-9-shaped envelope |

Isolation: export read path must not share failure modes with grading write path (E3.a pattern extended).

Policy: [`audit_sink_failure_policy.md`](../policy/audit_sink_failure_policy.md) §3 Critical.

---

### Q7 — Schema versioning

**Pinned:** API **v1**, additive fields only. Response carries `schema_version`, `harness_version`, `blueprint_content_hash`. Regulator pins HTTP major version; rubric schema pinned via envelope blueprint version.

---

## Closed pins (reviewer answers — 2026-08-22)

1. **Q1 MVP:** `get_session_evidence` (F.a) + `list_learner_sessions` paginated cap 100 (F.b). **Date-range out of F.**
2. **Q4:** Demographics **out entirely**; name-strip at **projection**; field list above.
3. **`regulator_export`:** **Out of F** (named exclusion table).
4. **NCVET snapshot mandate:** **None assumed, none built**; revisit on explicit regulator requirement only.

---

## Pinned invariants (I-F-1…7 — pin before F.a code)

| ID | Invariant |
|----|-----------|
| **I-F-1** | **`ncvet_audit` reads are read-only projections** over append-only sources. No mutable export table in F. **Read path queries `runtime.session_evidence_projection` only** (never `session_ledger` directly). Write path projects from ledger + corpus at finalize. |
| **I-F-2** | **Query kinds are a closed enum.** MVP: `get_session_evidence`, `list_learner_sessions`. Extended only by explicit phase work. |
| **I-F-3** | **Scope check precedes query execution.** Split scopes: `ncvet:read_session_evidence`, `ncvet:read_learner_sessions`. Write scopes do **not** imply read. Regulator token carries `caller_kind=ncvet_audit`. |
| **I-F-4** | **Tenant enforced at scope AND query layer.** One tenant per token; cross-tenant does not exist. |
| **I-F-5** | **Projection-layer redaction.** Demographics never selected; free-text name fields never selected. Field list pinned in this doc; additions require design review. |
| **I-F-6** | **Fail-closed on audit sink failure.** Same policy as `get_retirement_history`: 503, no rows in body. **Zero grading-pipeline dependency** on `ncvet_audit` availability. |
| **I-F-7** | **Pagination mandatory on list kinds; cap 100; per-page audit.** Cursor (not offset); cursor in `query_params_hash` (E3.b.2 discipline). Aggressive regulator pagination visible in audit. |

---

## Slice plan

Order: **F.a → F.b → F.c**. **`regulator_export` out of F.**

### F.a — `get_session_evidence` + scopes + projection redaction (**closed — signed off 2026-08-22**)

**Goal:** One end-to-end audited read: single session evidence bundle, fail-closed, projection-layer redaction proof — same gate shape as E3.a.

**Landings (review summary):**

| Pin | Shape |
|-----|-------|
| Closed `caller_kind` | `caller_kinds.py` + migration 012 + `validate_audit_event` at insert |
| I-F-1 (strong) | AST import guard + SQL spy on projection table; no `SessionLedgerRow` on read path |
| I-F-5 (sharpened) | Full JSON text grep for excluded keys at any depth; co-requirement: flat JSON only (no encoded blobs) |
| I-F-6 | Fail-closed 503 + `audit_sink_failure_total` increment proven; grading-path grep |
| 404 vs 503 | Missing projection → 404 + audit `outcome=error`, `error_kind=not_found`; sink failure → 503 |
| SQLite test pool | `<main>.runtime` attach — **SQLite dialect only**; Postgres uses `CREATE SCHEMA` |
| Deploy | [`012_ncvet_session_evidence_projection_checklist.md`](../migrations/012_ncvet_session_evidence_projection_checklist.md) |

**Tests:** `tests/test_authoring_f_a.py` — **12 cases** (9 original + fail-closed metric + 404 audit + nested grep + dialect guard).

#### Deliverables

| Area | Change |
|------|--------|
| **Scopes** | Add `ConsentScope.NCVET_READ_SESSION_EVIDENCE` → `ncvet:read_session_evidence` (tenant-scoped grants) |
| **Query kind** | Register `get_session_evidence` in `KNOWN_QUERY_KINDS` + `_REGISTERED_KINDS`; `assert_known` at emit |
| **Read service** | `NcvetAuditReadService` (or extend audited ledger read) — loads session by `session_id`, tenant-bound |
| **Projection** | SQL/ORM projection selects **allowlist fields only** — demographics subtree omitted at source; document in module comment |
| **Case context** | Join corpus row for session’s `(tenant_id, case_id, case_version)` — envelope slice without demographics |
| **Audit wrapper** | `AuditingNcvetReadService` — one `ledger_read_audit` row per HTTP call; `caller_kind=ncvet_audit`; `actor_subject_id` required |
| **Fail-closed** | Add `get_session_evidence` to `_FAIL_CLOSED_KINDS` (or F-specific set); 503 envelope matches I-E3-9 |
| **HTTP** | `GET /v1/ledger/session_evidence/{session_id}` (or query param — pick one in implementation, document here when landed) |
| **Gateway** | Scope deny → audit row + 403 before query; tenant mismatch → 403 + audit |

#### Files (expected)

| File | Change |
|------|--------|
| `shared/schemas/ledger_read.py` | `ConsentScope` + grant strings |
| `ledger_read/query_kinds.py` | `get_session_evidence` live set |
| `ledger_read/ncvet_*.py` (new) | Service + audit wrapper + projection |
| `ledger_read/routes.py` | Route + deps wiring |
| `tests/test_authoring_f_a.py` (new) | Acceptance below |

#### Tests (`test_authoring_f_a.py`)

1. **Happy path:** scoped token → 200 → evidence fields present; demographics **absent** from JSON (grep structural).
2. **Scope deny:** no grant → 403 + audit `consent_scope`; query not executed.
3. **Tenant mismatch:** wrong tenant param → 403 + audit `tenant_mismatch`.
4. **Fail-closed:** sink fails on ok read → 503 envelope; **no evidence in body**; emit attempted.
5. **Projection proof:** response JSON must not contain keys under `demographics` / pinned exclude list (regression for I-F-5).
6. **Grading isolation:** finalize/append path does not import or call ncvet gateway (static/grep guard).
7. **Closed enum:** unknown `query_kind` raises at registration/wrapper init.

#### Non-goals (F.a)

- `list_learner_sessions` (F.b)
- Pagination cursors (F.b)
- `regulator_export`
- Date-range filters
- Observability counters (F.c optional)
- Demographics export

---

### F.b — `list_learner_sessions` + cursor pagination (**closed — signed off 2026-08-22**)

**Goal:** Learner-scoped session list with E3.b.2 pagination discipline + I-F-7. Same audited `/v1/ledger/` prefix; **distinct scope** from F.a (`ncvet:read_learner_sessions`).

**Four pre-open pins — confirmed in F.b.1 diff:**

| Pin | Landing |
|-----|---------|
| Cross-learner cursor replay | Cursor embeds `learner`; decode rejects mismatch → 400 + `cursor_invalid` audit |
| `page_ordinal` server-derived (I-F-7) | Set at `encode_ncvet_cursor`; hashed in `ncvet_list_audit_params` — not client input |
| Index review | Migration **013** adds `(tenant_id, learner_pseudo_id, finalized_at_utc DESC, session_id DESC)` — 012 alone insufficient |
| Mid-pagination 503 | `list_learner_sessions` route docstring: fail-closed any page, no partial body, cursor preserved |

#### Pins (must land before first F.b test green)

| # | Pin |
|---|-----|
| **1** | **Cursor pagination only — no offset.** Keyset on `(finalized_at_utc DESC, session_id DESC)` within `(tenant_id, learner_pseudo_id)` filter. Opaque base64 cursor (client cannot forge tenant-spanning scan). **Cap 100**; `limit=101` → 400. Rationale: concurrent projection writes make offset pagination lie (same as E3.b retirement history). |
| **2** | **Per-page audit exfil shape (I-F-7).** One `ledger_read_audit` row per HTTP page. Hashed params include `cursor`, `limit`, `learner_pseudo_id`, and **`page_ordinal`** (1-based page index for this logical query) so E3.c.b runbook exfil-burst SQL covers NCVET list reads with the same detector shape as retirement history. |
| **3** | **Scope split preserved.** `ncvet:read_session_evidence` does **not** imply `ncvet:read_learner_sessions`. Scope-split matrix in tests (session-only grant, list-only grant, both, neither). |
| **4** | **Fail-closed on list kind.** Add `list_learner_sessions` to NCVET `_FAIL_CLOSED_KINDS`; 503 envelope matches I-E3-9; assert `audit_sink_failure_total` increment (E3.c.a discipline). |
| **5** | **Migration 012 + 013 before deploy.** 012 for projection table + `ncvet_audit` CHECK; **013** for keyset index (012 `(…, finalized_at DESC)` lacks `session_id` tie-breaker). |

**Follow-up boundary:** If projection redaction needs a second pass (structured PII over free-text), that is **F.a.1** — not scope folded into F.b.

#### Slice sequence (E3.b-shaped)

| Slice | Deliverable |
|-------|-------------|
| **F.b.1** | `list_learner_sessions` read service (projection table only), cursor encode/decode, gateway + audited wrapper, route, scope enum |
| **F.b.2** | Pagination + per-page audit tests; scope-split matrix; fail-closed + metric assertion |

#### F.b.1 deliverables

| Area | Change |
|------|--------|
| **Scope** | `ConsentScope.NCVET_READ_LEARNER_SESSIONS` → `ncvet:read_learner_sessions` |
| **Query kind** | Register `list_learner_sessions` in `KNOWN_QUERY_KINDS` + `F_NCVET_QUERY_KINDS` + `_REGISTERED_KINDS` |
| **HTTP** | `GET /v1/ledger/learner_sessions?learner_pseudo_id=…&limit=&cursor=` |
| **Read service** | `NcvetEvidenceReadService.list_learner_sessions` — queries `runtime.session_evidence_projection` only (I-F-1) |
| **Cursor** | Composite keyset: `WHERE tenant_id = ? AND learner_pseudo_id = ? AND (finalized_at_utc, session_id) < cursor_tuple ORDER BY finalized_at_utc DESC, session_id DESC LIMIT ?` |
| **Cursor wire format** | Opaque base64 JSON `{"at": "<iso8601>", "sid": "<session_id>"}` — same discipline as `catalog_cursor.py` |
| **Response** | `NcvetLearnerSessionsPage` — `{ items: [session summary…], next_cursor }`; summary fields from projection allowlist (no case_context blob on list — session_id + grades + timestamps only) |
| **Audit** | `AuditingNcvetReadService.list_learner_sessions`; `caller_kind=ncvet_audit`; params hash includes cursor + limit + learner + **page_ordinal** |
| **Gateway** | Scope deny / tenant mismatch before query (same as F.a) |

#### F.b.1 files (expected)

| File | Change |
|------|--------|
| `shared/schemas/ledger_read.py` | `NCVET_READ_LEARNER_SESSIONS`, `NcvetLearnerSessionSummary`, `NcvetLearnerSessionsPage` |
| `ledger_read/query_kinds.py` | `list_learner_sessions` live |
| `ledger_read/ncvet_cursor.py` (new) | Encode/decode + validation |
| `ledger_read/ncvet_service.py` | List method |
| `ledger_read/ncvet_audit.py` | List wrapper + fail-closed kind |
| `ledger_read/ncvet_gateway.py` | List entry + page_ordinal tracking |
| `ledger_read/auth.py` | `for_ncvet_read_learner_sessions()` |
| `ledger_read/routes.py` | Route |

#### F.b.2 tests (`test_authoring_f_b.py`)

1. **Happy path:** scoped token → 200 → N summaries; audit `outcome=ok`; `result_row_count == len(items)`.
2. **Two pages:** distinct `query_params_hash` per page; `page_ordinal` 1 then 2; cursors chain.
3. **Limit cap:** `limit=101` → 400.
4. **Scope deny:** no list grant → 403 + audit; query not executed.
5. **Scope split:** session-only vs list-only vs both vs neither (4-case matrix).
6. **Tenant mismatch:** 403 + audit `tenant_mismatch`.
7. **Fail-closed:** sink fails on ok page → 503; no items in body; `audit_sink_failure_total` += 1.
8. **Fail-closed mid-pagination:** page 1 ok, page 2 sink fails → 503; page-1 cursor still valid on retry.
9. **Cross-learner cursor replay:** learner A cursor + learner B query → 400 + audit `cursor_invalid` (not `200 []`).
10. **Projection proof:** list SQL touches projection only; AST guard unchanged from F.a.
11. **Closed enum:** bogus kind at wrapper init → `RuntimeError`.
12. **Zero rows:** empty learner → 200 + `items=[]`; audit with `result_row_count=0`.

#### Non-goals (F.b)

- Date-range / tenant-wide filters
- `regulator_export`
- Case context envelope on list rows (detail fetch via F.a `get_session_evidence`)
- Prometheus counters (F.c optional)
- F.a.1 redaction expansion (separate slice if needed)

---

### F.c — Observability + runbook (**closed — signed off 2026-08-22**)

**Goal:** Runbook + belt tests mirroring E3.c.b. Metrics landed in F.b (`ledger_read_ncvet_total`); F.c adds ops artifacts and audit/metric pairing proofs.

**Gate:** Closed with F.c.b sign-off.

#### Pins

| Pin | Detail |
|-----|--------|
| **I-E3-10 NCVET** | Option (a) landed F.b: `ncvet_safe_emit` sole call site for `record_ncvet_audit_metrics`. Option (b) belt: `test_i_e3_10_single_call_site_for_record_ncvet_audit_metrics` in `test_authoring_f_c_a.py`. |
| **Separate counter family** | `ledger_read_ncvet_total` / `ledger_read_ncvet_rows_returned_total` — not shared with catalog labels. |
| **Exfil SQL** | Extend catalog slow-drip shape: `caller_kind='ncvet_audit'`, `page_ordinal` in hashed params for `list_learner_sessions`, distinct `query_params_hash` per page. |
| **Thresholds** | TBD placeholders only — ops baseline pass fills numeric values. |
| **Non-goals** | Grafana/Alertmanager JSON in repo; audit schema changes; new query kinds. |

#### Slice sequence (E3.c-shaped)

| Slice | Deliverable |
|-------|-------------|
| **F.c.a** | Grep belt test (**closed**); `ncvet_metrics.py` / `ncvet_audit.py` module anchors |
| **F.c.b** | Runbook `docs/ops/ledger_read_ncvet_observability.md` + I-E3-11 parity test (**closed**) |

#### F.c.b runbook contents (expected)

**Scope:** `get_session_evidence`, `list_learner_sessions` — `caller_kind=ncvet_audit`.

**Axioms section:**

- I-E3-10 NCVET pairing (`ncvet_safe_emit` ↔ audit row ↔ `ledger_read_ncvet_total`)
- I-F-6 fail-closed: 503 does not increment ok ncvet counters; use `audit_sink_failure_total`
- I-F-7: `page_ordinal` server-derived; list exfil uses distinct `query_params_hash` per page
- Three-way `result_row_count`: NULL (deny / cursor_invalid / not_found), 0 (ok empty), >0 (ok data)
- Framework 422 (e.g. `limit=101`) silent to audit surface — no row, no metric

**PromQL (first-class, thresholds TBD):**

1. Scope probe — `ledger_read_ncvet_total{outcome="scope_denied",error_kind="consent_scope"}`
2. Tenant probe — `error_kind="tenant_mismatch"`
3. Cursor tamper — `outcome="error",error_kind="cursor_invalid"` (includes cross-learner replay; `result_row_count IS NULL` in SQL companion)
4. Exfil rows rate — `ledger_read_ncvet_rows_returned_total`
5. Exfil page rate — `ledger_read_ncvet_total{outcome="ok",error_kind=""}`
6. Sink failure — `audit_sink_failure_total{sink="primary"}` (shared with catalog; both surfaces fail-closed)

**SQL sketches:**

6. Distinct-page exfil — `caller_kind='ncvet_audit'`, group by `actor_subject_id`, `COUNT(DISTINCT query_params_hash)` (list kind benefits from `page_ordinal` in hash)
7. Cross-tenant probe — distinct `tenant_id` per actor, `tenant_mismatch` filter
8. Scope-probe actor drill-down — `consent_scope` denies
9. Cursor reconnaissance — `error_kind='cursor_invalid' AND result_row_count IS NULL` (probe signal, not empty-page ok)
10. Session not-found probe — `error_kind='not_found'` rate per actor (optional companion)

**Playbooks:** One section per alert (scope, tenant, cursor, exfil burst, sink failure) — same 3am shape as catalog runbook.

**Related:** [`ledger_read_catalog_observability.md`](../ops/ledger_read_catalog_observability.md), migrations 012/013 checklists.

#### F.c tests

| File | Cases |
|------|-------|
| `test_authoring_f_c_a.py` | I-E3-10 grep belt (**closed**) |
| `test_authoring_f_c_b.py` | I-E3-11 deny-path metric/audit parity: scope / tenant / cursor + `NcvetMetricsError` on unknown `error_kind` (**closed**) |

---

## Phase F closeout

**Phase F is closed** (signed off 2026-08-22). Acceptance summary:

| Slice | Deliverable | Tests |
|-------|-------------|-------|
| **F.a** | `get_session_evidence` + projection redaction + scopes | `test_authoring_f_a.py` (12) |
| **F.b.1–F.b.2** | `list_learner_sessions` + cursor + metrics + deny matrix | `test_authoring_f_b_1.py` + `test_authoring_f_b.py` (12) |
| **F.c.a** | I-E3-10 NCVET metrics/audit sole-call-site belt | `test_authoring_f_c_a.py` (1) |
| **F.c.b** | Observability runbook + I-E3-11 deny-path parity | `test_authoring_f_c_b.py` (2) + [`ledger_read_ncvet_observability.md`](../ops/ledger_read_ncvet_observability.md) |

**Full F suite:** 27 green.

### Boundary pin (Phase F closed surface)

| Constraint | Value |
|------------|-------|
| **HTTP surface** | All NCVET regulator reads under `/v1/ledger/` |
| **Audit identity** | `caller_kind=ncvet_audit`; `actor_subject_id` required |
| **Read path** | Projection-only (`runtime.session_evidence_projection`) — never `session_ledger` on read |
| **Live query kinds** | `get_session_evidence`, `list_learner_sessions` only |
| **Named exclusions** | Date-range session queries; **`regulator_export`** / bulk envelope export — **out of F**, not deferred into F |

Crossing any named exclusion requires an explicit Phase G (or later) pin — not an "enhancement" on F routes.

### Deploy reminders

- Migrations **012 → 013 → app code** (checklists in `docs/migrations/`)
- Scopes: `ncvet:read_session_evidence`, `ncvet:read_learner_sessions`
- Ops baseline pass fills TBD thresholds in both catalog and NCVET runbooks (parallel, doc-only)

### Explicit non-goals (Phase F entire — frozen)

- **`regulator_export`** — out of F, not deferred
- Date-range / tenant-wide bulk session export
- Demographics in export shape
- NCVET snapshot / quarterly artifact generation
- Push, streaming, SFTP
- Mutable export table
- Cross-tenant regulator super-scope
- Grafana / Alertmanager JSON in repo (runbook PromQL only)

### What opens next

| Order | Item | Notes |
|-------|------|-------|
| **1** | **Phase G scope pin** | Seven-questions **in review** — [`authoring_harness_phase_g.md`](./authoring_harness_phase_g.md). Starts from the **boundary pin** above — not from re-opening F. |
| **—** | **Ops baseline pass** | Parallel; fill numeric thresholds in [`ledger_read_ncvet_observability.md`](../ops/ledger_read_ncvet_observability.md) and catalog runbook when 7d scrape completes |
| **HARD GATE** | **Regulator-onboarding calendar URL** | Pin wiki/anchor in the NCVET scope-probe playbook **before first NCVET on-call rotation opens.** If the calendar surface is not ready, NCVET on-call does **not** start; fail-closed 503 holds the line until escalation is real. |

**Tracked blocking item (same gate, two surfaces):** until the calendar URL is pinned in [`ledger_read_ncvet_observability.md`](../ops/ledger_read_ncvet_observability.md) scope-probe playbook:

1. **NCVET on-call rotation** does not open.
2. **Scope-probe response** with an unresolved TBD does **not** improvise — use the documented fail-closed / ticket default (known-client grant fix → ticket; unrecognized sustained probe → page only after calendar check is possible). No ad-hoc escalation path while the URL is unset.

### Phase G opening ground rules (frozen at F closeout)

1. **Boundary pin values are inputs**, not open questions — HTTP `/v1/ledger/`, `caller_kind=ncvet_audit`, projection-only, live kinds `{get_session_evidence, list_learner_sessions}`, named exclusions for date-range and `regulator_export`.
2. **Any proposal touching a named exclusion** opens with a scope-crossing justification against the frozen table — not an amendment to F.
3. **Any proposal touching HTTP prefix, `caller_kind`, read path, or the live-kinds enum** is a boundary crossing and needs explicit justification.
4. **Trust-ladder framing** expected for G scope draft: audit record → signed credential → replayable competency proof (F closed the audit surface; G is where credential issuance / RCP verifier machinery start to matter).
5. **Review pattern:** seven questions → pins → invariants → slice plan → diff.
