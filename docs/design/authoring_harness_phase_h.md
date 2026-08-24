# Authoring harness — Phase H (Replayable Competency Proof)

Status: **Phase H closed (countersigned 2026-08-23).** H.a + H.b + H.c.1 + H.c.2.1 + **H.closeout** **closed**.  
Parent: [`authoring_harness.md`](./authoring_harness.md) — Appendix C (trust ladder).  
Prior: [`authoring_harness_phase_g.md`](./authoring_harness_phase_g.md) (**G.a + G.b closed**), [`authoring_harness_phase_f.md`](./authoring_harness_phase_f.md) (**closed**), [`authoring_harness_phase_e.md`](./authoring_harness_phase_e.md) (**closed**).

**Review pattern:** seven questions → pins → invariants → slice plan → H.a → H.b → H.c → **closeout**.

**Ladder (locked naming):** audit record (F) → signed credential (G) → **Replayable Competency Proof (H)**.

---

## What Phase H is (and is not)

**Goal:** Ship **rung 3** — deterministic **recompute-under-same-rubric** against a sealed transcript (projection-over-append-only), producing a **portable signed** regrade artifact / RCP package, without touching F live kinds or G’s five scopes.

| Rung | Artifact | Phase |
|------|----------|-------|
| 1 | Audit record | F **closed** |
| 2 | Signed credential + status list / offline verify | G **closed** |
| 3 | **Replayable Competency Proof** | **Closed — this phase** |

**RCP is not** “Regulator Credential Protocol.”

### Named exclusions (not “deferred” — **out of Phase H**)

| Exclusion | Justification |
|-----------|---------------|
| Re-opening F live `/v1/ledger/` kinds | F/G freeze |
| Public `evidence_ref` status | G recon-oracle exclusion |
| Bulk issuance / bulk RCP mint | Same creep as G Q1 |
| Fully-online-only verify bypassing G.b | Assist ≡ offline |
| Sticky Option-B / Option C staleness | G.b |
| Dropping rung 2 (VC) | Ladder climbs |
| Direct ledger transcript `SELECT`/write from re-grade | Q1 — projection-only / immutable-by-grant |
| Silent digest fallthrough | Q1.4 |
| Folding regrade audit into `ledger_read_audit` | Q2 — schemas diverge |
| Internal-only (unsigned) artifact as MVP end-state | Q5 — retrofit cost |
| Raw `session_id` (or learner pseudo-id) in portable envelope | Amendment 1 / I-G-8 extended |
| **Score-under-different-rubric** | **Different operation semantically** — new grading under a new rubric; requires alternate-rubric authorization surface, grade coexistence / supersede rules, and credential-surface semantics **not in scope for H**. Opens later as its **own slice against the H boundary**, not as an H amendment. Same discipline as `regulator_export` out of F. |

### Deferred (scaling — not scope exclusions)

| Item | Revisit when |
|------|----------------|
| Async re-grade **queue** (ops enqueue / worker dequeue) | **Calendar anchor: revisit numerics by 2026-09-22** (≈30 days after H.a countersign) **or 30 days after first H.a production deploy**, whichever is later. Then pin: open async-queue slice when **p95 regrade latency exceeds X** *or* concurrent regrade depth exceeds **Y** (X/Y filled from that review). Until then synchronous HTTP MVP (I-H-7). |
| Durable grade-pending queue (grader success → artifact write) | Ops show retry+idempotency window insufficient (Q6) |

---

## Frozen inputs

### G / F boundary (unchanged)

G five scopes zero-implication; offline verify authoritative; status-list pins 1–3; `ncvet_issuer` / `ncvet_verifier`; F kinds frozen; assist ≡ offline; revoke ≠ ref-revoke.

### Q1 four pins (frozen)

| # | Pin |
|---|-----|
| **1** | `SELECT` only on `runtime.session_transcript_projection` |
| **2** | Zero write grant on `session_transcript_ledger` |
| **3** | Fresh grade → `regrade_artifact`; link `(session_id, transcript_digest)`; own audit surface |
| **4** | Recompute digest vs sealed; mismatch → audit + no grader + no artifact write |

### Three roles of `session_id` (Amendment 2 — pin table)

| Role | Where | Portable? |
|------|-------|-----------|
| **(a) Authenticated server input** | `POST /v1/regrade/session/{session_id}` path — tenant + scope checked **before** handler | **No** |
| **(b) Internal join key** | `regrade_audit.session_id`, `regrade_artifacts.session_id` | **No** |
| **(c) Portable envelope field** | — | **Forbidden** — never in header, nested provenance, or comment |

Opaque **`regrade_id`** (artifact id, same shape) is the only regrade identifier that crosses the trust boundary in the portable envelope. Correlation `regrade_id` → `session_id` requires an authenticated audit/DB query.

### Mapping table (Amendment 1)

| Table | Role |
|-------|------|
| **`regrade_artifacts`** | `(regrade_id, session_id, transcript_digest, …)` — join lives on the **audit/issuer side**; portable envelope carries `regrade_id` only among identifiers |

Grep: `test_h_a_portable_envelope_excludes_session_id_by_grep` (F projection-redaction shape).

### Seven-question pin summary (countersigned + amendments)

| Q | Pin |
|---|-----|
| **Q2** | `caller_kind=ncvet_regrader`; dedicated `regrade_audit`; one-row-per-attempt |
| **Q3** | HTTP `POST /v1/regrade/session/{session_id}`; scope `ncvet:regrade_session` |
| **Q4** | Two-digest audit row; projection carries `transcript_digest` per row |
| **Q5** | Portable signed envelope; G.b sig discipline; **opaque `regrade_id` only** |
| **Q6** | Fail-closed all modes; grading-pipeline isolation; retry+idempotency on artifact write |
| **Q7** | Three-version tuple; **recompute-under-same-rubric** (sealed); different-rubric **named exclusion** |

---

## Pinned invariants (I-H-1…14)

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **I-H-1** | Portable envelope contains only opaque **`regrade_id`** / artifact id among session-correlation identifiers; **no** `session_id`, **no** learner pseudo-id in portable fields (any nesting) | **`test_h_a_portable_envelope_excludes_session_id_by_grep`** (+ pseudo-id grep) |
| **I-H-2** | Sealed **rubric_schema_version** pinned at regrade time; recompute is deterministic against that version. Score-under-different-rubric is **out of H** | Artifact field assert + exclusion table; matrix sealed-rubric case |
| **I-H-3** | Regrade scopes (`ncvet:regrade_session`, …) distinct from G’s five; **zero implication** either direction | Scope-deny matrix cells |
| **I-H-4** | `regrade_audit` / `regrade_artifacts` carry `session_id` (internal join); portable envelope does **not** | Join test + I-H-1 grep |
| **I-H-5** | Path `{session_id}` is authenticated server input; **tenant + scope check precedes handler** | 403 before side effects; no audit success row on deny |
| **I-H-6** | Fail-closed on audit-sink failure — **no** `regrade_artifacts` row if unauditable (F/G discipline) | 503 + absence test |
| **I-H-7** | Synchronous HTTP execution in MVP; async queue **deferred** with named revisit thresholds (not a silent default) | No worker/queue modules in H.a package greps |
| **I-H-8** | Named exclusions table is authoritative — score-under-different-rubric, unsigned-internal-only MVP, ledger direct access, etc. | Doc pin + negative-space tests where applicable |
| **I-H-9** | Transcript read = **projection only** (`runtime.session_transcript_projection`); inherits F flat/allowlist discipline | Import / SQL / grant greps |
| **I-H-10** | Re-grade path has **zero write grant** on `session_transcript_ledger` | Negative-space wiring test |
| **I-H-11** | Digest **recompute-and-assert before grader**; mismatch → `error_kind=transcript_digest_mismatch`, audit row, **no grader**, **no artifact**. **Digest algorithm inherited from grader-digest brief track** (same interlock as G I-G-3/I-G-7). H.a opens against whichever alg is pinned at diff time; until brief lands, use shared placeholder id `sha256-canonical-json-v0` — **do not invent a second default**. | Matrix mismatch case; alg id fixture |
| **I-H-12** | Dedicated **`regrade_audit`** + **`ncvet_regrader`**; **one row per attempt** (success and failure) | Audit cardinality + enum tests |
| **I-H-13** | Original session-grading pipeline has **zero dependency** on re-grade availability; regrade invokes grader as **pure function over sealed inputs** via a narrow adapter (no shared mutable grading module import either direction) | Isolation grep / import boundary |
| **I-H-14** | Portable artifact is **signed** with a **distinct regrade-issuer key** (`regrade_issuer_key_id`) — **not** the G credential issuer key (**option b**). `signed_at` + `valid_until` inside signed payload (G.b discipline). Key published alongside credential keys on the status-list / JWKS surface so verifiers pull one trust root for both; H.b wires verify without treating regrade envelopes as credentials. Nonsense / wrong-key → reject closed. | Sign/verify matrix; wrong-key case |

**Boundary sentence:** *H recomputes under the sealed rubric via projection-only transcript read; it does not re-author grades onto the transcript ledger, does not score under a live alternate rubric, and does not put `session_id` on the wire.*

---

## Slice plan

| Slice | Deliverable | Status |
|-------|-------------|--------|
| **H.a** | Regrade E2E — projection + digest gate + `regrade_audit` / `regrade_artifacts` + portable signed envelope + scopes + `ncvet_regrader` | **Closed** — 15/15 |
| **H.b** | Verify / present portable RCP; uniqueness + concurrency; audit two-digest success path; retention; fail-closed metrics | **Closed** — 15/15 |
| **H.c** | Observability / metrics / runbook (surface labels; Shape A verify audit; entropy; greps) | **Closed** — H.c.1 9/9 + H.c.2.1 5/5 |
| **H.closeout** | Sign-off — acceptance suite, deferred list, invariants index, ops checklist | **Closed** |

Score-under-different-rubric / async queue → **not** H slices; see exclusions / deferred.

---

## H.a detail — **closed** (countersigned 2026-08-23)

**Goal:** One end-to-end recompute-under-same-rubric for one sealed session — F.a / G.a-shaped gate.

### Route

| Method | Path | Scope | Behavior |
|--------|------|-------|----------|
| `POST` | `/v1/regrade/session/{session_id}` | **`ncvet:regrade_session`** | Tenant+scope gate → projection read → digest assert → grader (sealed rubric) → append `regrade_audit` + `regrade_artifacts` → return **portable signed** envelope (**no** `session_id`) |

**Prefix:** `/v1/regrade/` — distinct from `/v1/ledger/` and `/v1/credentials/` (threat model / middleware isolation).

### `caller_kind` + migration

| Kind | Phase | Use |
|------|-------|-----|
| `ncvet_regrader` | **H.a** | Regrade attempts (audit emits) |

Migration **016** (sketch): `regrade_audit`, `regrade_artifacts`, `runtime.session_transcript_projection` (if not pre-existing), CHECK adds `ncvet_regrader`. Checklist: apply-in-prod-before-deploy (012/014/015 discipline). Same commit: `KNOWN_AUDIT_CALLER_KINDS` + emitters.

### Storage sketch

| Object | Role |
|--------|------|
| `session_transcript_ledger` | Append-only SoT — **no** regrade writes |
| `runtime.session_transcript_projection` | Read surface; carries **`transcript_digest`** per row |
| `regrade_audit` | One row per attempt; two digests; outcome fields (Q4) |
| `regrade_artifacts` | `(regrade_id, session_id, transcript_digest, grade_payload, grader_version, rubric_schema_version, artifact_schema_version, signed bytes, …)` |

### Portable envelope (closed set — I-H-1 / I-H-14)

**Include:** `regrade_id`, `transcript_digest`, `grade_payload`, `grader_version`, `rubric_schema_version`, `artifact_schema_version`, `regraded_at`, `signed_at`, `valid_until`, `issuer_id` / `key_id`, proof; optional `credential_id` binding (pin in H.a impl if needed for ladder join).  
**Exclude:** `session_id`, learner pseudo-id, demographics, full projection payload, ledger row ids.

### Files (expected)

| File | Change |
|------|--------|
| `audit/caller_kinds.py` | Add `ncvet_regrader` |
| Migration `016_*` + checklist | Tables + CHECKs + projection if needed |
| `shared/schemas/…` | `ConsentScope.NCVET_REGRADE_SESSION` |
| `regrade/*.py` (new package) | Service, sign, routes under `/v1/regrade/` |
| Grader boundary | Invoke grader with **sealed** rubric version only; no live-corpus alternate |
| `tests/test_authoring_h_a.py` | Matrix below |

### Test matrix (`test_authoring_h_a.py`) — acceptance spec (**15 cases**)

**Count note:** Scope deny and tenant deny are **separate** cases (5 and 7) — not one combined cell. That yields 14 through sync-MVP; case **15** is wrong-key signature reject (I-H-14 option b).

| # | Test | Protects |
|---|------|----------|
| **1** | **Happy regrade** — scoped caller → 200 → portable body closed fields; opaque `regrade_id`; grade payload present; signature under `regrade_issuer_key_id` | I-H-1, I-H-14 |
| **2** | **`test_h_a_portable_envelope_excludes_session_id_by_grep`** — recursive / wire grep; also no learner pseudo-id keys | I-H-1, I-H-4 |
| **3** | **Sealed rubric pin** — seal rubric v1, advance live authoring rubric to v2, regrade asserts v1 grading logic / `rubric_schema_version=v1` | I-H-2 |
| **4** | **Digest mismatch** — tampered projection → audit `transcript_digest_mismatch`, `grader_outcome=not_invoked`, **no** artifact | I-H-11, I-H-12 |
| **5** | **Scope deny** — missing `ncvet:regrade_session` → 403; no artifact | I-H-3, I-H-5 |
| **6** | **G scope non-implication** — issue/verify scopes alone do not authorize regrade | I-H-3 |
| **7** | **Tenant deny** — cross-tenant `session_id` → 403/404; no artifact (**14th was ambiguous — this is the distinct tenant cell**) | I-H-5 |
| **8** | **Fail-closed audit sink** — sink fail → 503; **no** `regrade_artifacts` row | I-H-6 |
| **9** | **Closed `caller_kind` / 016 belt** — `ncvet_regrader` accepted; unknown rejected; 016 SQL includes kind | I-H-12 |
| **10** | **Audit attempt cardinality** — mismatch + happy each → exactly one `regrade_audit` row; retry → N rows | I-H-12, Q4 |
| **11** | **Projection-only / ledger write isolation** | I-H-9, I-H-10 |
| **12** | **Grading isolation** — finalize/append ↛ regrade; regrade ↛ grading package (sealed adapter only) | I-H-13 |
| **13** | **No new F ledger query kinds** | I-H-8 / F freeze |
| **14** | **Sync MVP grep** — no queue/celery/`create_task` in regrade package | I-H-7 |
| **15** | **Wrong-key signature** — verify portable envelope with G credential issuer key (or unknown key) → reject (`signature_invalid` / bad sig) | I-H-14 option b |

### H.a non-goals

- Portable RCP **verify** HTTP assist beyond unit verify in tests (H.b)
- Score-under-different-rubric
- Async queue
- Binding credential mint at regrade time
- Public transcript-digest oracle
- Changing G.a/G.b routes or F kinds

### Gate

**H.a countersigned closed 2026-08-23** — 15/15 green; isolation-grep held.

### H.a closed notes — countersign asks answered

| Ask | Status |
|-----|--------|
| **016 enum discipline** | Matches 012/014/015: `KNOWN_AUDIT_CALLER_KINDS` + `ck_audit_caller_kind` CHECK + same-commit emitters; `regrade_audit` also CHECKs `caller_kind='ncvet_regrader'` |
| **Immutability** | Doc pin: `regrade_audit` / `regrade_artifacts` INSERT-only from regrade path; no UPDATE grant. **H.b** lands write-site grep + checklist line (H.a checklist amend) |
| **`(session_id, transcript_digest)` UNIQUE** | **Gap at H.a close** — today PK is `regrade_id` only; indexes are non-unique. **H.b lands UNIQUE** (migration **017** or 016 forward-fix) + race → `error_kind=regrade_duplicate` |
| **Isolation grep** | AST denylist on **module path** (`services.pratibimb.grading` / `grading` segment) — Amendment-2 shape; not symbol-name |
| **Digest-before-grade** | H.a asserts `grader_outcome=not_invoked` + no artifact. **H.b** strengthens: stub call-counter == 0 on mismatch |
| **Envelope closed set** | Fields listed in H.a; **allowlist decoder + extra-key reject** deferred to **H.b** (F.b.1 cursor shape) |

**Landed correctly:** distinct `ncvet_regrader`; `/v1/regrade/` + `ncvet:regrade_session`; sealed stub + opaque `regrade_id`; `grader_version` already on `regrade_artifacts` row.

---

## H.b — Verify + harden (**countersigned contingents locked — H.b.1 opening**)

**Goal:** Portable RCP **verify / present** path + close H.a hardening gaps (uniqueness, concurrency, two-digest success symmetry, retention pin, fail-closed metrics). Does **not** open score-under-different-rubric or async queue.

### Contingent pins (locked before H.b.1 — 2026-08-23)

| # | Pin |
|---|-----|
| **C1** | Migration **017** applies **before** H.b.1 app deploy (012/014/015/016 discipline). UNIQUE `(tenant_id, session_id, transcript_digest)` on `regrade_artifacts`. |
| **C1b — race discipline** | **Invoked-then-lost (MVP):** both callers may pass digest + invoke sealed grader; INSERT UNIQUE decides winner. Loser: audit row with `grader_invoked=true`, `grader_outcome=success` (grade computed but discarded), `error_kind=regrade_duplicate`, **no** artifact row for loser. Audit consumers: `regrade_duplicate` **does** imply possible doubled grader work. Lock-then-grade = named future stretch (not H.b). |
| **C2 — caller_kind** | Verify-assist emits / tags under existing **`ncvet_verifier`** (G.b). Scope `ncvet:verify_regrade` distinguishes authority; no new `ncvet_regrade_verifier`. Rationale: same operational class as credential verify (holder-presented envelope, offline-verdict). Pattern: one caller_kind per operational class; scopes split authority (F `ncvet_audit` × two query kinds). |
| **C3 — grader_version** | Sourced from **import-time constant** `regrade.sealed_grade.GRADER_VERSION` only — not env/runtime config. Matrix asserts artifact + envelope match that constant. |

### Pre-open pins (from H.a countersign)

| # | Pin |
|---|-----|
| **1** | **Two digests unconditionally** on success audit — both non-null and equal |
| **2** | Artifacts **immutable-once-written**; `grader_version` load-bearing (C3) |
| **3** | Fail-closed 503 + `audit_sink_failure_total{surface="regrade", …}` increment. **`surface` closed set:** `catalog` \| `ncvet` \| `credentials` \| `regrade` (document; free-form forbidden) |
| **4** | Concurrent / sequential second insert → UNIQUE + `regrade_duplicate` (C1b) |

### Routes / surfaces

| Method | Path | Scope | `caller_kind` | Behavior |
|--------|------|-------|---------------|----------|
| `POST` | `/v1/regrade/session/{session_id}` | `ncvet:regrade_session` | `ncvet_regrader` | Unchanged + UNIQUE / duplicate (H.a + 017) |
| `POST` | `/v1/regrade/verify` | **`ncvet:verify_regrade`** | **`ncvet_verifier`** | Assist; assist ≡ offline |
| `GET` | `/v1/regrade/artifacts/{regrade_id}` | `ncvet:verify_regrade` | `ncvet_verifier` | Tenant-bound fetch of stored envelope |
| — | Offline lib | — | — | Allowlist decode + sig under regrade key |

### Scopes (four-way matrix required)

| | has `regrade_session` | lacks `regrade_session` |
|--|----------------------|-------------------------|
| **has `verify_regrade`** | both | verify-only |
| **lacks `verify_regrade`** | regrade-only | neither → 403 both |

Zero implication either direction.

### Test matrix (`test_authoring_h_b.py`) — **15 cases (acceptance spec)**

| # | Test | Protects |
|---|------|----------|
| **1** | Offline verify happy — allowlist decode + regrade key → accept | I-H-14 |
| **2** | Assist ≡ offline parity (accept + reject) | G.b discipline |
| **3** | **`test_h_b_success_audit_carries_both_digests`** | Pre-open 1 |
| **4** | Mismatch — stub **call_count == 0**; audit `not_invoked` | I-H-11 |
| **5** | Allowlist rejects extra key (e.g. `session_id_hint`) | Closed set |
| **6** | Wrong / G-issuer key reject (lib + assist) | I-H-14 b |
| **7** | **`test_h_b_concurrent_regrade_second_hits_unique_constraint`** — second attempt → `regrade_duplicate`; one artifact; loser audit `grader_invoked=true` | C1b / pre-open 4 |
| **8** | 017 SQL UNIQUE belt (grep) | C1 |
| **9** | Fail-closed audit → 503; no artifact; **`audit_sink_failure_total` with `surface="regrade"` increments** | Pre-open 3 |
| **10** | `grader_version` == `GRADER_VERSION` import constant on row + envelope | C3 / pre-open 2 |
| **11** | Four-way scope matrix (verify-only / regrade-only / both / neither) | C2 scopes |
| **12** | `GET /artifacts/{regrade_id}` tenant-bound; forged id → 404 | Opaque id |
| **13** | No new F ledger kinds | Freeze |
| **14** | Write-site AST grep — no UPDATE on `regrade_audit` / `regrade_artifacts` | Immutability |
| **15** | Verify path does not imply / call regrade mint (import + scope) | Zero implication |

### H.b non-goals

- Score-under-different-rubric; async queue; lock-then-grade; new verify caller_kind; bulk regrade; public enumeration

### Gate

**Contingents locked 2026-08-23** — open migration **017 first**, then H.b.1 code against matrix.

### Gate

**H.b.1 countersigned closed 2026-08-23** — 15/15 (+ H.a 15/15 = 30). Verify-assist landed without disturbing H.a mint surface.

### H.b closed notes — countersign clarifications

| Ask | Confirmation |
|-----|----------------|
| **UNIQUE excludes `grader_version`** | **Yes** — one artifact per `(tenant, session, transcript)`. Grader bump → still 409 duplicate until an **explicit re-issue path** lands (named exclusion / later). Documented in 017 checklist. |
| **017 rollback** | Mirror of deploy: **app first**, then drop UNIQUE. Documented in 017 checklist. |
| **Allowlist / trust root** | Field allowlist = code-embedded frozenset; expected regrade key id = import-time / deploy default (`DEFAULT_REGRADE_ISSUER_KEY_ID` + HMAC secret env for material) — **not** fetched at first-verify. Offline-capable. |
| **`GET /artifacts/{id}`** | `{id}` = opaque **`regrade_id`** (same identifier as portable envelope). Not a separate artifact id; no session reverse-index. |
| **Four-way scope matrix** | (a) regrade-only (b) verify-only (c) both (d) neither — landed in `test_h_b_11_four_way_scope_matrix`. Mint audit = `ncvet_regrader` + `regrade_session`; verify path does not write `regrade_audit` (verifier class via scope; `caller_kind=ncvet_verifier` when verify audit emits in H.c if required). |
| **Call-counter == 0** | On digest mismatch (and scope deny never reaches grader) — structural I-H-11 belt |

**Carry-forwards → H.c** (from H.b countersign):

1. Observability runbook names **all closed `surface` labels** for `audit_sink_failure_total`
2. 017 rollback ordering (done in checklist — H.c runbook links it)
3. `grader_version` exclusion from UNIQUE (done in checklist — H.c runbook links it)

---

## H.c — Observability (**H.c.1 closed**; H.c.2.1 ready)

**Goal:** Runbook + metric discipline for G credentials + H regrade surfaces; Shape A verify-assist audit; entropy pin. **No new product routes** beyond audit emit on existing verify/fetch callbacks. **No UNIQUE widening.**

### Six pins (countersigned before H.c.1 — 2026-08-23)

| # | Pin |
|---|-----|
| **1 — Runbook surface table** | Four rows: `catalog` \| `ncvet` \| `credentials` \| `regrade`. Each: PromQL, threshold, on-call **role** (not person), escalation. Thresholds **provisional pending ops baseline — review by 2026-09-22**. |
| **2 — Alert firing** | Fire on **`rate > 0`** with **~2m suppression**. Audit sink failures are fail-closed → sustained non-zero ⇒ user-visible 503s. |
| **3 — Metric grep call sites** | Test docstring names exact expected `AUDIT_SINK_FAILURE_TOTAL` inc sites: catalog, ncvet, credentials (status-list), regrade mint, regrade Shape A verify. Every `surface=` ∈ closed set. |
| **4 — Verify-assist audit = Shape A** | Unconditional emit on verifier→NAADI callbacks: `POST /v1/regrade/verify`, `GET /v1/regrade/artifacts/{regrade_id}`. Offline verify does not emit. `caller_kind=ncvet_verifier`. Sink fail → 503. |
| **5 — Surface enum stays four** | No `regrade_verifier` surface; use sub-labels if needed. |
| **6 — `regrade_id` entropy** | `REGRADE_ID_ENTROPY_BYTES = 32` (same floor as evidence_ref). |

### Closed `surface` set

`catalog` \| `ncvet` \| `credentials` \| `regrade`

```text
sum by (surface) (rate(audit_sink_failure_total[5m]))
```

### Test matrix (`test_authoring_h_c.py`)

| # | Test | Protects |
|---|------|----------|
| **1** | Runbook lists four surfaces + provisional thresholds + review date + role on-call | Pin 1 |
| **2** | Runbook states `rate > 0` + suppression | Pin 2 |
| **3** | Grep `surface=` ⊆ closed set | Pin 3 / 5 |
| **4** | Named call sites for sink-failure inc; regrade site present | Pin 3 |
| **5** | Shape A — verify assist emits `ncvet_verifier`; offline no emit | Pin 4 |
| **6** | Shape A — artifact GET emits; sink fail → 503 | Pin 4 |
| **7** | `REGRADE_ID_ENTROPY_BYTES == 32` | Pin 6 |
| **8** | 017 checklist linked (deploy, rollback, grader_version exclusion) | Carry-forwards |
| **9** | Mint audit ⊆ `{ncvet_regrader}`; verify callback ⊆ `{ncvet_verifier}` | Mint ≠ verify |

### Gate

**H.c.1 countersigned closed 2026-08-23** — 9/9 (+ H.b 15/15 + G.b 23/23).

### H.c.1 closed notes — countersign clarifications

| Clarification | Landed |
|---------------|--------|
| **Threshold review owner + artifact** | Runbook: `role:authoring-platform-oncall-lead`; artifact = PR against `credentials_regrade_observability.md` with production-signal data (review by 2026-09-22) |
| **Emit-then-respond** | `emit_regrade_verify_audit` docstring + gateway comments: audit before HTTP success body |
| **Closed surface set** | Remains `catalog` \| `ncvet` \| `credentials` \| `regrade` (not `authoring` / `ledger`) |
| **Five sites / four surfaces** | `regrade` = mint + Shape A; asymmetry is load-bearing |

**Carry-forwards → H.c.2:**

1. ~~Threshold review owner + artifact~~ — **done in H.c.1 closeout** (runbook)
2. Grep meta-test asserts five sites by **`file:function`**, not presence-only — diagnostic failure mode (F.a I-F-5 shape)

---

## H.c.2 — Grep diagnostic harden (**H.c.2.1 closed**)

**Goal:** Tighten pin-3 meta-test so a consolidation refactor fails with a named missing `file:function`, not an off-by-one. Observability-only; **no product routes; no UNIQUE widening; no surface-enum change.**

### Pins (countersigned before H.c.2.1 — 2026-08-23)

| # | Pin |
|---|-----|
| **1 — Exact `file:function` table** | Five anchors; per-anchor assert → `"{anchor} not found in registered fail-closed sites"`. |
| **2 — Closed surface set unchanged** | Literal set; accretion vs erosion messages distinct. |
| **3 — Mint ≠ Shape A kinds** | Intersection empty with named colliding kind; mint file greps literal `_SHAPE_A_QUERY_KINDS` member values. |
| **4 — Exactly five labeled fail-closed sink-failure sites** | Extras → checklist message; docstring scopes + inverse four-part landing. |

### Test matrix (`test_authoring_h_c_2.py`)

| # | Test | Protects |
|---|------|----------|
| **1** | Per-anchor `file:function` presence | Pin 1 |
| **2** | Literal closed set; unexpected vs missing surface messages | Pin 2 |
| **3** | Mint ∩ Shape A empty; mint file literal kind values | Pin 3 |
| **4** | Exactly five; extras named with runbook/PromQL/threshold checklist | Pin 4 |
| **5** | Runbook lists same file/function strings (manual sync MVP) | Carry-forward 1 |

### Gate

**H.c.2.1 countersigned closed 2026-08-23** — 5/5 (+ H.c.1 9/9). Diagnostics self-locating; docstring scoped.

### H.c.2.1 closed notes — countersign clarifications

| Clarification | Landed |
|---------------|--------|
| Per-anchor loop (not batched len) | `test_h_c_2_1` / `test_h_c_2_4` |
| Accretion vs erosion surface messages | `test_h_c_2_2` |
| Collision names kind + both sets | `test_h_c_2_3` |
| Extra-site checklist message | `test_h_c_2_4` |
| Docstring exclusions (a)(b)(c) + inverse four-part landing | module + `test_h_c_2_4` docstrings |
| Manual runbook↔registry sync | `test_h_c_2_5` (generate-from-registry = H.closeout stretch) |

**Carry-forwards → H.closeout:**

1. Optional: generate runbook call-site table from `_EXPECTED_FAIL_CLOSED_SITES` (stronger than manual sync).
2. First threshold-review PR by **2026-09-22** owned by `role:authoring-platform-oncall-lead` — do not let provisional thresholds calcify.

---

## H.closeout — Phase H sign-off (**closed 2026-08-23**)

**Phase H is closed.** Rung 3 (Replayable Competency Proof) landed without opening score-under-different-rubric or async queue. Ground rules below are **frozen at H closeout** — downstream phases consume them as inputs; do not renegotiate at open.

**Ops checklist:** [`h_phase_closeout_checklist.md`](../ops/h_phase_closeout_checklist.md)

---

### Acceptance matrix

| Slice | Deliverable | Suite / artifact | Count |
|-------|-------------|------------------|-------|
| **H.a** | Regrade E2E — projection, digest gate, portable signed envelope | `test_authoring_h_a.py` | **15** |
| **H.b** | Verify / present RCP; UNIQUE; Shape A prep; fail-closed metrics | `test_authoring_h_b.py` | **15** |
| **H.c.1** | Runbook; Shape A audit; entropy; surface labels | `test_authoring_h_c.py` | **9** |
| **H.c.2.1** | Fail-closed sink-failure meta-test (`file:function`) | `test_authoring_h_c_2.py` | **5** |
| **H.closeout** | Acceptance marker + inventory belt | `test_authoring_h_acceptance.py` | **2** |

**H acceptance suite (single invocation):**

```bash
pytest -m h_acceptance
```

**Expected:** **44/44** green on H.a + H.b + H.c.1 + H.c.2 modules (`h_acceptance` marker in `pyproject.toml`). Wire into CI / release gate for rung-3 contract.

**Runbook sync posture (pin 4):** manual `test_h_c_2_5` MVP — registry and [`credentials_regrade_observability.md`](../ops/credentials_regrade_observability.md) kept in sync by test. Codegen table-from-registry = deferred stretch (see below).

---

### Boundary pin (Phase H closed surface)

| Constraint | Value |
|------------|-------|
| **HTTP prefix** | `/v1/regrade/` only (distinct from `/v1/ledger/`, `/v1/credentials/`) |
| **Mint audit** | `regrade_audit` + `caller_kind=ncvet_regrader` |
| **Verify audit** | `ledger_read_audit` + `caller_kind=ncvet_verifier` (Shape A on callback only) |
| **Transcript read** | `runtime.session_transcript_projection` only — zero write on ledger |
| **Portable id** | Opaque `regrade_id` only — no `session_id` in envelope |
| **UNIQUE** | `(tenant_id, session_id, transcript_digest)` — **`grader_version` excluded** |
| **Observability surfaces** | `catalog` \| `ncvet` \| `credentials` \| `regrade` (four only) |
| **Named exclusions** | Score-under-different-rubric; async queue; fifth surface; F kind expansion |

Crossing any named exclusion requires explicit scope-crossing justification — not an H amendment.

---

### Deferred list (frozen — reason + trigger + calendar)

Re-examine at **2026-09-22 ops review** (separate line-items on agenda — not folded into threshold-only).

| Item | Reason deferred | Reopen trigger | Calendar checkpoint |
|------|-----------------|----------------|---------------------|
| **Async re-grade queue** | I-H-7 synchronous HTTP MVP; worker topology is ops scaling, not rung-3 contract | **p95 regrade latency > X** *or* concurrent regrade depth **> Y** (X/Y from prod baseline) | **2026-09-22** or 30d after first H.a prod deploy (whichever later) |
| **Score-under-different-rubric** | Different operation — alternate-rubric auth, grade coexistence, credential semantics not in H | Regulator / product requires re-grade under live rubric with explicit authorization surface | **2026-09-22** agenda item + explicit new phase pin |
| **Durable grade-pending queue** | Q6 retry+idempotency sufficient for MVP | Ops shows artifact-write retry window insufficient | **2026-09-22** or incident-driven |
| **Codegen runbook from registry** | Manual `test_h_c_2_5` MVP sufficient at closeout | Drift incident or second surface expansion | **2026-09-22** stretch review |

---

### Threshold review (pin 5 — artifact-shaped)

| Field | Pin |
|-------|-----|
| **Date** | On or before **2026-09-22** |
| **Owner (role)** | `role:authoring-platform-oncall-lead` |
| **Artifact** | **Merged PR** against `credentials_regrade_observability.md` with production-signal data **and** per-surface threshold decision (hold provisional \| promote fixed \| revise) |
| **Checklist** | [`h_phase_closeout_checklist.md`](../ops/h_phase_closeout_checklist.md) — checkbox requires PR URL, not "reviewed" alone |

---

### Ground rules (frozen at H closeout)

1. **Boundary pin values are inputs** for any post-H work touching regrade — not open questions.
2. **Trust ladder complete at rung 3** for this lineage: F audit → G credential → H RCP. Parallel tracks (Samvaad, regulator UI) consume H artifacts; they do not reopen H without scope-crossing pin.
3. **H acceptance suite name is stable:** `pytest -m h_acceptance` → cite **44/44** when asserting rung-3 regression.
4. **Deferred items** reopen only via trigger + calendar review — not silent scope creep.
5. **I-H-* invariants index** (below) is authoritative for boundary questions — grep from downstream work.

---

### Invariants index (survives Phase H)

Reference when boundary questions arise. Enforcement = primary test or structural constraint.

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **I-H-1** | Portable envelope: opaque `regrade_id` only among session-correlation ids; no `session_id` / learner pseudo-id | `test_h_a_portable_envelope_excludes_session_id_by_grep`; H.b allowlist |
| **I-H-2** | Sealed `rubric_schema_version` at regrade; score-under-different-rubric **out of H** | `test_h_a_3_sealed_rubric_not_live`; exclusion table |
| **I-H-3** | Regrade scopes distinct from G five; zero implication | `test_h_a_6_*`; `test_h_b_11_four_way_scope_matrix` |
| **I-H-4** | Internal join on `session_id`; portable envelope excludes it | I-H-1 grep + artifact schema |
| **I-H-5** | Path `{session_id}` authenticated; tenant+scope before handler | `test_h_a_5_*`, `test_h_a_7_*` |
| **I-H-6** | Fail-closed audit sink → no artifact row; 503 | `test_h_a_8_*`; `test_h_b_9_*`; H.c Shape A 503 |
| **I-H-7** | Sync HTTP MVP; no async queue in regrade package | `test_h_a_14_sync_mvp_no_queue_imports` |
| **I-H-8** | Named exclusions authoritative | Doc + negative-space greps (F kinds, etc.) |
| **I-H-9** | Transcript read = projection only | `test_h_a_11_*` |
| **I-H-10** | Zero write grant on `session_transcript_ledger` | `test_h_a_11_*` |
| **I-H-11** | Digest recompute-and-assert before grader; mismatch → no grader | `test_h_a_4_*`; `test_h_b_4_*` (call_count==0) |
| **I-H-12** | `regrade_audit` + `ncvet_regrader`; one row per attempt | `test_h_a_9_*`, `test_h_a_10_*` |
| **I-H-13** | Grading pipeline isolation — sealed adapter only | `test_h_a_12_grading_isolation_both_ways` |
| **I-H-14** | Distinct regrade-issuer key; signed portable artifact | `test_h_a_15_*`; H.b verify matrix |

**Observability belt (H.c):** closed four-surface enum; five fail-closed sink-failure sites at H closeout — `test_authoring_h_c_2.py`. **I.a** extends to six (`arp/service.py:_write_audit`, surface=`regrade` until I.c).

---

### Deploy reminders

- Migrations **016 → 017 → app** (checklists in `docs/migrations/`)
- Runbook: [`credentials_regrade_observability.md`](../ops/credentials_regrade_observability.md)
- Closeout checklist: [`h_phase_closeout_checklist.md`](../ops/h_phase_closeout_checklist.md)

### Explicit non-goals (Phase H entire — frozen)

Score-under-different-rubric · async queue · lock-then-grade · fifth surface · UNIQUE widening · new F ledger kinds · bulk RCP mint · public transcript-digest oracle

### Gate

**H.closeout countersigned closed 2026-08-23** — acceptance suite pinned; deferred list frozen; invariants index consolidated.

---

## Next step

**Phase H closed.** Ops: first threshold-review **artifact** by **2026-09-22**.

**Phase I opened (post-ladder):** [`authoring_harness_phase_i.md`](./authoring_harness_phase_i.md) — alternate-rubric recompute against this boundary pin (score-under-different-rubric). Not a fourth trust-ladder rung.