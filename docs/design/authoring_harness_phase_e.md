# Authoring harness — Phase E (runtime corpus + ledger hash + audited reads)

Status: **Phase E closed (countersigned 2026-08-23).** Phase F closed ([`authoring_harness_phase_f.md`](./authoring_harness_phase_f.md)). Phase G — G.a detail in review ([`authoring_harness_phase_g.md`](./authoring_harness_phase_g.md)).  
Parent: [`authoring_harness.md`](./authoring_harness.md).  
Prior: [`authoring_harness_phase_d.md`](./authoring_harness_phase_d.md).

## Confirmed pins (2026-08-20)

### I1 — Ledger blueprint hash (Slice E1)

| Decision | Value |
|---|---|
| Field | `blueprint_content_hash VARCHAR(64) NULL` on `session_ledger` |
| Record field | `SessionLedgerRecord.blueprint_content_hash: Optional[str] = None` |
| Keep `EvidenceEventView.content_hash` | **= `replay_hash`** (trace). Do not overload. Add `blueprint_content_hash` as a new read field when wiring lands. |
| Source | Snapshotted at **session create** from `published_case_versions.content_hash` via `(tenant_id, case_id, case_version)` |
| Finalize | **Mandatory** verify: if published row resolves a hash and it differs from session snapshot → refuse append with `BLUEPRINT_HASH_DRIFT` |
| Mid-session retire | Stamp remains the **create-time snapshot**. No denormalized retirement flag on the ledger row; retirement is discoverable via join to `published_case_versions.retired_at` |
| Legacy / seed | `NULL` — never synthesize |
| Immutability | Set once at append; never rewritten |

### I2 — Runtime corpus (Slice E2)

| Decision | Value |
|---|---|
| Backing | DB table `runtime.published_case_corpus` |
| Trigger | **Post-commit** hook after publish/retire (not inside the authoring txn) |
| Self-heal | Startup rebuild + periodic reconciliation; `projection_stale` metric |
| Test seam | `refresh_now(tenant_id)` for sync refresh in tests |
| Seed fallback | Per-tenant: zero projected rows → `seed_corpus.json` only if `ALLOW_SEED_FALLBACK=true`; once ≥1 projected row, seed invisible forever. Prod: `ALLOW_SEED_FALLBACK=false` |
| Retire | Set `probe_only=True` on blueprint envelope at retire |

### I3 — Audited reads (Slice E3)

Locked 2026-08-20; detail in §E3 (**closed**).

| Decision | Value |
|---|---|
| Routes | `/v1/ledger/...` via `AuditingLedgerReadService`, not `/v1/authoring/*` as the audited surface |
| Scopes | `authoring:read_published` (list/get **metadata**, no full blueprint); `authoring:read_retirement_history` |
| Full blueprint export | Remains behind `ncvet_audit` as a distinct query kind (**out of E3 MVP**) |
| Query kinds | `get_published_case_versions`, `get_retirement_history` → `_REGISTERED_KINDS` |

## Slice E1 progress

### Landed (E1 close — evidence read + pick validation)

- `EvidenceEventView.blueprint_content_hash` — **write-time** from `SessionLedgerRecord` at append; view-read never re-resolves
- `content_hash` on the view remains `replay_hash` (trace)
- `CreateSessionRequest` Pydantic all-or-none pick → `invalid_case_pick` → HTTP **400** `INVALID_CASE_PICK`
- Internal `SessionManager` ValueError guard retained as belt-and-suspenders

### Next (out of E1)

- E2: `runtime.published_case_corpus` projection (post-commit)
- E3: audited ledger-read scopes + emit for reserved query kinds

## Sequencing

1. **E1** — ledger stamp + mandatory drift check ✅  
2. **E2** — DB corpus projection (post-commit) + sampler ← **scope below**  
3. **E3** — audited ledger-read emit for reserved query kinds

---

## Slice E2 — scope pass (pins locked 2026-08-21)

**Goal:** Replace the seed-JSON-only `CaseSampler` path with a tenant-scoped projection of published (and retired-as-probe) cases into `runtime.published_case_corpus`, so session create can load the **actual published blueprint** instead of overwriting a seed case's `case_id`.

E1 already stamps `blueprint_content_hash` from `authoring.published_case_versions`. E2 makes the **runtime physio/grading shape** come from the same published artifact. Until E2 lands, published-backed create still uses seed physio + identity bind (documented temporary seam in `SessionManager`).

### Why a projection (not read authoring live)

| Concern | Authoring live read | Projection |
|---|---|---|
| Sampler latency / hot path | Couples learner create to authoring schema + draft JSON | Corpus is the only read surface for sampling |
| Retire semantics | Must remember to filter `retired_at` everywhere | `probe_only` flag on corpus row; sampler excludes by default |
| Seed coexistence | Awkward dual path | Per-tenant seed fallback with irreversible cutover |
| Failure isolation | Publish txn failure vs sampler outage intertwined | Post-commit: authoring commit wins even if projection lags |

Authoring remains source of truth for publish/retire. Corpus is a **rebuildable cache** with self-heal.

### Pins (Q1–Q5 locked; E2.1 coding pins)

| # | Decision |
|---|---|
| **Q1** | PK = `published_case_versions.id`. No separate mapping UUID. Drop redundant `source_published_id` column — the PK *is* the source id. |
| **Q2** | Create-by-pick on corpus miss → **project-on-miss** (sync `project_one`, then read corpus). Never return live authoring JSON to the session path. Dual shape sources forbidden. |
| **Q3** | `envelope_json` stores **V2** blueprint (+ fixtures / provenance). V1 physio adapter lives at the session/engine boundary only — one corpus format. |
| **Q4** | Table lives in **`runtime.published_case_corpus`**. Migration file under **ledger** migration lineage (same Postgres, new schema). |
| **Q5** | **Explicit** projector call after authoring commit returns (route/facade). No SQLAlchemy `after_commit` hook. Easier to test; session lifecycle stays obvious. |
| **Pin 1** | `probe_only` (and `workflow_state`) **derived only** from `published.retired_at IS NOT NULL`. No `probe_only` parameter on `project_one`; no caller-supplied flag. |
| **Pin 2** | Re-project of an unchanged published row is **byte-idempotent** on `envelope_json` (raw JSON string equality via canonical dumps). `envelope_json` is TEXT, not JSONB, to preserve bytes. |

### E2.1 status

**Landed** — migration, ORM, `project_one` / `refresh_now`, A1–A10, Pins 1–2. See `test_corpus_projector_e2_1.py`.

### Table: `runtime.published_case_corpus` (landed)

| Column | Type | Notes |
|---|---|---|
| `id` | PK (str) | **=** `authoring.published_case_versions.id` |
| `tenant_id` | str | Partition key for sampler + seed fallback |
| `case_id` / `version` | str | Unique with tenant |
| `content_hash` | char(64) | Copied from published — never recomputed as write source |
| `assessment_mode` | str | Frozen at publish |
| `workflow_state` | str | `PUBLISHED` \| `RETIRED` (derived with probe_only) |
| `probe_only` | bool | Derived only from `retired_at IS NOT NULL` |
| `harness_version` / `published_at` | | From publish stamp |
| `envelope_json` | TEXT | Canonical JSON text — byte-idempotent on re-project |
| `projected_at` | timestamptz | Bumped on every project |

Envelope: `{workflow_state, published_at, harness_version, approver_subject_ids, fixtures, blueprint}` — frozen at project time; no draft re-join on read.

### Chunking

| Chunk | Deliverable | Gate |
|---|---|---|
| **E2.1** | Migration + ORM + `project_one` / `refresh_now` | ✅ closed |
| **E2.2** | Façade + reconcile + `blueprint_source` + `project_on_miss` + corpus runtime surface | ✅ **closed — projection is the runtime read surface** |
| **E2.3** | Operational reconciler (startup + periodic + lag metrics) | ✅ **closed** |
| **E2.4** | Residual SessionManager / sampler provenance matrix + E3 handoff | ✅ **closed** (c → **E2.5**) |
| **E2.5** | Corpus-backed `load_probe_case`; seed harness path preserved | ✅ **closed** — `test_authoring_e2_5.py` (10 green) |
| **E3** | Audited ledger-read scopes + emit | ✅ **closed** |

### Review deltas locked (2026-08-21)

| Delta | Pin |
|---|---|
| **D1** | Ledger `blueprint_source` ∈ `{published, seed}` (nullable = pre-E2.2 legacy unknown). Set at append: corpus/published path → `published`; seed path → `seed`. Distinguishes two null-stamp stories. Land column in **E2.2** (table still small). |
| **D2** | Load-bearing test: `create_session` for a **just-published, un-projected** pick returns success with corpus row present and stamp set — **inside that call** (project-on-miss). No client retry. Closes publish→hook race. |

### Status (pins §§1–5)

**Accepted** (path-split lag, both-path post-commit, session-start sampler, seed bypass, SessionManager boundary). Deltas D1–D2 locked into E2.2.

---

## E2.2 slice breakdown (landed a–e)

**Goal:** Wire post-commit projection + ledger provenance marker + prove project-on-miss closes the publish→create race. Pull only as much SessionManager pick path as D2 requires; leave full sampler/seed six-case and random-sample stamping to E2.3/E2.4.

### Why these three land together

Façade without project-on-miss leaves D2 untestable end-to-end. `blueprint_source` without a published create path has nowhere to set `published`. Minimal pick path without façade still needs heal-on-miss. One review gate.

### Out of E2.2

- Seed six-case table / `ALLOW_SEED_FALLBACK` sampler behavior (E2.3) — but **column** exists; seed writer lands with E2.3
- Random-sample corpus weighting / difficulty filters (E2.3)
- Dropping every remaining SessionManager seed overwrite for non-pick creates (E2.4 residual)
- Out-of-band queue (MVP reconcile = in-process callable; same entrypoint)

### Sub-slices

| Sub | Deliverable | Invariants |
|---|---|---|
| **E2.2.a** | Publish/retire façade → `project_one` after authoring commit; never fails HTTP | Publish/retire 200 when projector raises; `projection_stale` set on failure; cleared/absent on success |
| **E2.2.b** | Reconcile safety net (`reconcile_tenant` / `refresh_now`) | Missing rows projected; retire-flag drift healed; **hook then reconcile → exactly one “projected” success metric/audit event** (not two) |
| **E2.2.c** | Ledger migration + ORM: `blueprint_source` | Nullable `VARCHAR` CHECK `IN ('published','seed') OR NULL`; no invented backfill of legacy rows to `published` |
| **E2.2.d** | `ensure_corpus_row` / `project_on_miss` helper | **Only** sync runtime→projector path; code comment states why (explicit-pick identity). Signature takes published identity / id — no live envelope return |
| **E2.2.e** | Minimal SessionManager published-pick | Pick → corpus (miss → project_on_miss) → stamp = `corpus.content_hash`; `blueprint_source=published` at finalize/append; drop seed+`case_id` overwrite **on this path** |

### Invariants to pin before code

| ID | Invariant |
|---|---|
| **I-E22-1** | Authoring commit success is independent of projection outcome |
| **I-E22-2** | Reconcile after successful façade emits **no second** “projected” success count (decision idempotent — not merely byte-idempotent row). Failure→heal may emit one success. |
| **I-E22-3** | `blueprint_source` is write-once at ledger append; never updated; NULL only for pre-migration rows |
| **I-E22-4** | `blueprint_source=published` ⇒ `blueprint_content_hash IS NOT NULL`; `blueprint_source=seed` ⇒ hash IS NULL. (Enforce in writer; optional DB CHECK.) |
| **I-E22-5** | `project_on_miss` is the sole sync call from runtime into `CorpusProjector`; documented at call site |
| **I-E22-6** | D2: create_session(pick) with published row present and corpus empty → 200/success, corpus row exists afterward, stamp equals published.content_hash, session stored |
| **I-E22-7** | If `project_one` fails after published exists → no session stored; structured error (`CORPUS_PROJECT_FAILED` or equivalent); not a silent seed fallback |

### Load-bearing tests (E2.2 minimum)

1. Publish succeeds when projector raises; authoring row present; stale set
2. Retire → façade project → `probe_only=true`
3. Hook success then reconcile → **one** projected-success event
4. Failed hook then reconcile → row lands; one success event from reconcile
5. Migration/ORM: `blueprint_source` null \| published \| seed; reject other strings
6. **D2 race:** seed published in authoring only (no prior project) → `create_session` pick → corpus row + stamp + `blueprint_source=published` in one call
7. Writer: published path rejects `blueprint_source=seed` with non-null hash mismatch per I-E22-4 (or equivalent assert)

### Metrics / audit (E2.2.b detail)

- `authoring_corpus_projection_total{result="success"|"error"|"skipped"}`
- `skipped` = reconcile examined row, already projected and flags match, **no write** (this is how I-E22-2 is enforced)
- Optional structured log/audit line with same result enum — test asserts count of `success` == 1 across hook+reconcile happy path

### Status

**E2.2.a–e landed. E2.2 closed — projection is the runtime read surface.**

---

## Accepted product pins (E2 remaining — archive)

§§1–5 below were accepted 2026-08-21. Operational detail for implementation lives in the E2.2 slice breakdown above; E2.3/E2.4 still consume §§3–5.

### 1. Lag window semantics (publish-commit → `project_one` landed)

The window is real and accepted. Behavior is **path-split** — not one global policy:

| Path | During lag | Pin |
|---|---|---|
| **Explicit pick** `(tenant_id, case_id, case_version)` | If corpus row missing **and** authoring published row exists → **project-on-miss** (`project_one` sync), then read corpus. If published row absent → existing `PUBLISHED_CASE_NOT_FOUND` (fail closed, no session). | **Not (a)** — never hand live authoring/draft JSON to the session. **Not (c)** — do not treat a just-published triple as permanently unavailable. Closest to sync repair of (b) without a client-visible retry loop. |
| **Random sample** (no pick) | Sample **only** from corpus rows already present with `probe_only=false`. A version published milliseconds ago may be absent from the pool until projected — **accepted**. No live authoring fill-in for the missing row. | Pool-level eventual consistency (flavor of **(c)** for the *pool*, not for an explicit pick). |
| **Stamp / finalize** | Create-time `blueprint_content_hash` comes from the **corpus row’s `content_hash`** once the envelope is loaded (see §5). Finalize still verifies against `published_case_versions` (E1). A9 copy-only keeps them equal. | No dual hash sources. |

Rejected alternatives:
- **(a) resolve envelope from `published_case_versions`/draft and skip corpus** — reintroduces dual shape sources; forbidden by Q2.
- **(b) block with retryable `CORPUS_NOT_READY` on every pick miss** — worse UX when project-on-miss is cheap and deterministic; reserve a hard error only if `project_one` itself fails after published exists (`CORPUS_PROJECT_FAILED` / stale metric).

### 2. Post-commit façade shape — **both**

| Layer | Role |
|---|---|
| **Fast path** | After `publish_draft` / `retire_published` **returns** (authoring txn committed), façade calls `CorpusProjector.project_one(published_id)` on a **separate** corpus session. Exceptions caught: log + `authoring_corpus_projection_total{result="error"}` + set `projection_stale`; **do not** fail the HTTP publish/retire. |
| **Safety net** | Periodic / startup reconcile: find published rows missing from corpus or with retire-flag drift → `project_one` / `refresh_now(tenant)`. Same idempotent upsert as E2.1 (byte-idempotent envelope, bump `projected_at`). |

Rationale: hook alone loses events on process crash between commit and project; worker alone adds latency to the common path. **Both**, with worker tests proving idempotency when the hook already succeeded (second project = same envelope bytes, one row).

Explicit call (Q5) — not SQLAlchemy `after_commit`. Worker may be in-process periodic task for MVP; out-of-band queue is an ops upgrade that must preserve the same `project_one` entrypoint.

**E2.2 chunk deliverable:** façade wiring + stale metrics + reconcile entrypoint + tests (publish survives throw; retire flips via project; worker idempotent vs prior hook).

### 3. Sampler scope — **session-start case selection**

| Question | Pin |
|---|---|
| **What** | Weighted sample of **learner session cases** (today’s `CaseSampler` job). |
| **Consumer** | `SessionManager.create_session` only (runtime). Not a QA integrity probe API. |
| **Pool** | `runtime.published_case_corpus` where `tenant_id = ? AND probe_only = false`. |
| **Predicates** | Required: `tenant_id`, `NOT probe_only`. Optional filters (preserved from seed sampler): difficulty target, learner gaps / competency tags, demographics.state — applied against fields inside `envelope_json.blueprint` after parse. |
| **Return** | Blueprint from `envelope_json` (V2 → boundary adapter if physio still V1). `content_hash` from the corpus **row** column, not recomputed. |
| **Out of scope** | Monitoring “sample N rows and verify projection” — reconcile metrics / future admin tool, not `CaseSampler`. |

`load_probe_case(tenant_id, case_id)` loads `probe_only=true` corpus rows (retired versions) for explicit probe use — separate from random sample.

### 4. Seed fallback

| Question | Pin |
|---|---|
| **Trigger** | Per-tenant: `count(corpus rows for tenant) == 0` **and** `ALLOW_SEED_FALLBACK=true`. Zero means **no rows at all** (retired rows count as projected — seed stays off). |
| **Source** | Existing `seed_corpus.json` (non-`probe_only` entries), same file as today’s sampler. |
| **Write to corpus?** | **No.** Seed **bypasses** corpus — does not insert seed cases into `published_case_corpus`. Seed is a pre-projection bootstrap for empty tenants only. |
| **Stamp** | Seed path → `blueprint_content_hash=None` + `blueprint_source=seed` (D1). Explicit published pick never uses seed for the envelope. |
| **Cutover** | Irreversible per tenant: once ≥1 corpus row exists, seed is invisible forever even if all later retire. |
| **Prod** | `ALLOW_SEED_FALLBACK=false` → empty corpus ⇒ fail closed on sample (no silent empty success). |

Not “seed-from-published-versions-on-empty-corpus” — that is `refresh_now` / reconcile when authoring has publishes. Seed is only for tenants with **nothing projected yet** (greenfield / local). Null stamp + `blueprint_source=seed` lets auditors distinguish seed-mode from pre-E2.2 legacy null (`blueprint_source IS NULL`).

### 5. SessionManager integration boundary

| Concern | Pin |
|---|---|
| **Published pick** | Resolve corpus row by `(tenant_id, case_id, version)`; on miss → project-on-miss → read. Load envelope blueprint into session. **Drop** seed+`case_id` overwrite seam. |
| **Hash pin at create** | `SessionState.blueprint_content_hash = corpus_row.content_hash` (column copied at project time). Do **not** re-resolve a second live hash for the stamp when corpus supplied the row. |
| **Finalize (unchanged E1)** | `append_graded_session` still verifies stamped hash against `published_case_versions` via `verify_blueprint_hash_at_finalize`. Drift / not-found codes unchanged. |
| **Consistency** | A9 ⇒ corpus `content_hash` == published `content_hash` ⇒ create stamp and finalize agree. |
| **Random create** | Sampler (§3) → if seed fallback, stamp null; if corpus sample, stamp = that row’s `content_hash` + bind `tenant_id` / `case_version` from the row. |
| **Retire mid-flight** | Unchanged: stamp freezes create-time hash; finalize ignores `retired_at` on resolve. |

### Sub-chunk acceptance (pointer)

Detailed E2.2 gates: see **E2.2 slice breakdown** (I-E22-1…7). E2.3 six-case + E2.4 residual SessionManager remain after E2.2 closes.

### Explicit non-goals (E2 remaining)

- Un-retire / delete corpus rows
- Learner-facing “this case is retired” UI
- Cross-tenant shared catalog projection
- Replacing `authoring.published_case_versions` as lifecycle SoT
- E3 scopes / query-kind emit
- Changing E1 finalize drift semantics
- Live authoring JSON on the session create path
- Using seed to backfill corpus

### Status

**E2.2 closed.** Corpus is the runtime read surface for published cases: ledger stamp binds grades to bytes (`blueprint_content_hash` + `blueprint_source='published'`), envelope loads from `envelope_json`, seed is one-way-flipped off per tenant once any row is projected. Boundary commit for future readers: E2.2.e.

**E2.2.c note:** migration `010_session_blueprint_source.sql`.  
**E2.2.d/e:** `project_on_miss` sole sync heal; SessionManager pick/sample stamp `source` + envelope bytes.

---

## E2.3 — Reconciler for hook-drop recovery (**pinned**)

**Goal:** Guarantee eventual consistency between `published_case_versions` and `runtime.published_case_corpus` when the post-commit façade never runs or fails — without depending on events.

**Already landed (E2.2.b — do not re-litigate):**
- `project_after_authoring_commit(..., idempotent=True)` → `"skipped"` when current
- `reconcile_tenant(tenant_id)` → walks published rows → façade with `idempotent=True`
- Decision-idempotent metric: hook success then reconcile → one `success`, one `skipped`
- Stale gauge clear **only** on façade `success`

**E2.3 is the operational layer on top of that callable** — not a second projection semantics.

### Job (invariant)

| Role | Authority |
|------|-----------|
| **Hook / façade** (`idempotent=False`) | Latency fast path after publish/retire commit |
| **Reconciler** | Safety net: closes publish-committed ↔ corpus gap when hook drops (process crash between commit and façade, worker death, corpus DB partition, swallowed façade error). Bounds random-pool / ops lag — **not** the explicit-pick heal path. |
| **`project_on_miss`** | Sync heal for **explicit** session create only. Two latency contracts → two mechanisms. |

If reconciler ever writes a different `envelope_json` than the hook would have for the same published row → **E2.1 regression**, not an E2.3 bug. E2.3 only schedules the same `project_one` / façade path.

### Scope pin

| In | Out |
|----|-----|
| Startup full sweep per tenant (bounded concurrency) | Event-driven / queue / outbox as reconciler trigger |
| Periodic sweep (**60s** pinned) | Changing `project_one` / envelope bytes / CHECK constraints |
| Lag gauge (oldest-stale) + reason-labeled reconciler counters incl. `manual` | Making random-sample wait on lag (pool lag remains OK per E2.2 §3) |
| Reuse `reconcile_tenant` / façade | Second projector implementation |
| Fail-soft per tenant (one tenant error must not abort others) | Blocking process boot forever on reconcile failure |
| MVP multi-instance: duplicate passes → cheap `skipped` | Multi-instance lease table (revisit when metric says races aren't cheap) |

### Trigger pin — **not event-driven**

| Trigger | Behavior |
|---------|----------|
| **Startup** | After authoring startup validation, schedule (or run) one full `reconcile_tenant` per known tenant with published activity. Bounded concurrency (**N=4** default). Boot must not wait unboundedly. |
| **Periodic** | Background loop every **60s**. Same `reconcile_tenant` entrypoint. |
| **Manual** | Ops/tests call `reconcile_tenant(..., reason="manual")` — must not pollute `startup`/`periodic` counters. |

**Interval: 60s pinned.** Env `CORPUS_RECONCILE_INTERVAL_SECONDS` (clamp 15–600). Rationale: bounds how long a hook-drop can leave the random-pool sampler serving a stale publishable set; “next minute” is inside human patience for publish→see-in-sampler. Steady state is overwhelmingly `skipped` / cheap `_is_projection_current`; if periodic cost climbs, raise the clamp — do not preemptively choose 300s.

### Metric pin (E2.3.a — **shipped**, Option B)

| Metric | Labels | Meaning |
|--------|--------|---------|
| `corpus_reconciler_row_total` | `tenant`, `reason`, `outcome` | One increment per published row examined in a reconcile pass. `reason ∈ {startup, periodic, manual}`. `outcome ∈ {success, skipped, error}` (mirrors façade). Helper rejects unknown labels (`ValueError`). |
| `corpus_reconciler_pass_total` | `tenant`, `reason`, `outcome` | One increment per **tenant pass** in a driver sweep. `outcome ∈ {ok, error}`. Pass-level (tenant call completed vs raised) — distinct from row_total so raise-before-rows does not invent fake row errors. |
| `corpus_reconciler_lag_seconds` | `tenant` | Gauge: age of the **oldest** stale row — `now − min(published_at)` over rows failing `_is_projection_current`. **0** when no stale rows. `compute_reconciler_lag_seconds(..., now=)` — `now` required (no wall-clock default in the pure helper). |

**Why `outcome` on the counter (amendment):** multi-instance MVP is “duplicate passes → cheap skipped.” `outcome=skipped` climbing at ~N× tenant×rows across N instances is fine; `outcome=success` climbing at N× is the race signal (check-then-act window wider than assumed). Visible without a lease table.

**Lag is (a), not (b):** oldest un-projected/`not current` row age (SLO / worst-case sampler staleness), **not** “how recent is the newest stale publish.”

**Relationship to façade metrics:** façade still emits `authoring_corpus_projection_total{result}` + stale gauge. Reconciler series are the ops view (who triggered the pass; multi-instance race shape).

### Lag definition (exact)

```
stale = { published | not _is_projection_current(published, corpus_row) }
lag_seconds(tenant) =
    0                              if stale is empty
    now_utc − min(p.published_at for p in stale)   otherwise
```

Retired published rows still count when corpus missing/stale (heal retire flags — E2.2.b).

### Slice breakdown

| Sub | Deliverable | Acceptance |
|-----|-------------|------------|
| **E2.3.a** | Lag helper + metric registration + unit tests; `reconcile_tenant` grows `reason=` (default `manual`); **no scheduler** | ✅ `test_corpus_reconciler_e2_3a.py` |
| **E2.3.b** | Driver: `tick_once` + startup then periodic loop; DISTINCT tenant discovery; fail-soft; shutdown within one tick; lifespan wire; interval 60s clamp 15–600 | ✅ `test_corpus_reconciler_e2_3b.py` |
| **E2.3.c** | Startup `asyncio.Semaphore(N)` — default 4, clamp 1–16; periodic stays sequential; fail-soft under gather | ✅ `test_corpus_reconciler_e2_3c.py` |

Order: **a → b → c** (observability proven before loops; startup last — lifespan ordering).

### Acceptance (E2.3 closed when)

1. Hook-drop sim: publish commits, façade never called → within one periodic tick (or startup) corpus current + byte-equal to direct `project_one`.
2. Fully projected tenant: reconcile → façade `skipped`; lag **0**; reconciler `outcome=skipped` increments, not spurious `success`.
3. Retire after missed hook: reconcile heals `probe_only` / `workflow_state`.
4. Lag reports oldest stale age; climbs between broken passes; drops to 0 after heal.
5. No event bus / publish-triggered reconcile path.

### Non-goals (explicit)

- Replacing `project_on_miss`
- Seed six-case / ALLOW_SEED_FALLBACK beyond E2.2.e
- Removing legacy `(hash, NULL source)` escape hatch
- Multi-instance lease (MVP until metrics say otherwise)

---

## E2.4 — Residual provenance matrix + Phase E2 closeout (**pinned**)

**Goal:** Close the last SessionManager / sampler provenance gaps so Phase E2 hands E3 a complete publish → project → sample/pick → stamp → finalize story — without reopening E2.2/E2.3.

### Pins (2026-08-22)

| Pin | Decision |
|-----|----------|
| Six-case matrix | **Yes** — single suite; **#4** fail-closed with same EmptyCorpusError as #3 (no OnlyProbeOnlyError) |
| E2.4.c load_probe_case from corpus | **Defer** (E2.5 / Phase F). Seed-file path stays; comment names it temporary |
| Hatch signal | **Counter** ledger_legacy_hatch_total{tenant} only (singular hatch — no reason label). Lands in **b** after **a** review |

### Six-case decision table

| # | Preconditions | Expected |
|---|---------------|----------|
| **1** | Corpus wired; tenant projected ≥1; random create | source=published, hash = corpus row, envelope bytes held |
| **2** | Corpus wired; empty tenant; seed fallback on; random create → finalize | source=seed, hash NULL on session **and** ledger (I-E22-4 / I-E24-1) |
| **3** | Corpus wired; empty tenant; seed fallback off; random create | EmptyCorpusError — no session stored |
| **4** | Corpus wired; only probe_only rows; random create | **Same** EmptyCorpusError as #3 |
| **5** | Corpus wired; explicit pick; corpus warm | envelope from corpus; source=published; seed path not used |
| **6** | No corpus_session (legacy hatch); explicit pick | source=NULL, hash from authoring resolve |

### Slice order

| Sub | Deliverable | Status |
|-----|-------------|--------|
| **E2.4.a** | Six-case suite + seed create→finalize→ledger; load_probe_case E2.5 comment | ✅ |
| **E2.4.b** | Hatch comment ↔ ledger_legacy_hatch_total{tenant} cross-ref; increment at hatch | ✅ |
| **E2.4.c** | load_probe_case from corpus | **→ E2.5** |

### Invariants

| ID | Invariant |
|----|-----------|
| **I-E24-1** | Seed ledger stamp is (hash=NULL, source=seed) on wired seed-fallback path |
| **I-E24-2** | Six-case matrix is exhaustive for create provenance under corpus wiring |
| **I-E24-3** | Legacy hatch does not gain new call sites in E2.4 |
| **I-E24-4** | Random-sample lag remains accepted — no project-on-miss on sampler |
| **I-E24-5** | #4 uses EmptyCorpusError — empty-eligible reason is diagnostic, not a new exception class |

### Explicit non-goals (E2.4)

- Removing legacy hatch
- E2.4.c corpus load_probe_case (deferred)
- Multi-instance reconciler lease / E3 scopes
- Seed rows into corpus / making random sample wait on lag

---

## E2.1 acceptance archive (landed)

| # | Criterion | Result |
|---|---|---|
| A1–A10 | See prior review; `test_corpus_projector_e2_1.py` (13 green) | ✅ |
| Pin 1 | `project_one(self, published_id)` only; CHECK `probe_only ↔ workflow_state` | ✅ |
| Pin 2 | Raw string equality on re-project envelope | ✅ |

---

## E3 — Audited ledger-read scopes (**closed**)

**Goal:** Make catalog reads of published / retired cases first-class audited ledger-read query kinds, with the same least-privilege care as `authoring:approve` vs `authoring:approve_summative`.

This is a **scope surface**, not a new projector. Reads hit **authoring SoT tables** (`published_case_versions`, `retired_case_versions`), not `runtime.published_case_corpus`. Corpus lag does not apply.

Reuse existing machinery: `AuditingLedgerReadService` + `ledger_read_audit` + `KNOWN_QUERY_KINDS`. Do **not** emit `authoring_audit` rows for reads — that table is write/lifecycle (harness §3.4). Conflating them is how an auditor loses the distinction between “who published” and “who listed the catalog.”

### Q1 — Which query kinds exist?

| Kind | In E3? | Why |
|------|--------|-----|
| `get_published_case_versions` | **Yes** | Reserved Phase D §6; tenant catalog of published (and optionally retired) **metadata** |
| `get_retirement_history` | **Yes** | Reserved Phase D §6; who/when/why from `retired_case_versions` |
| Live kinds (`learner_evidence`, …) | Already live | Out of E3 — do not re-litigate |
| Session-ledger / provenance-stamp reads | No | Already on `EvidenceEventView` (write-time `blueprint_content_hash` / `blueprint_source`) |
| Corpus-diff / projection QA | No | Ops (`corpus_reconciler_*`), not an auditor catalog kind |
| Full blueprint / envelope export | No | I3: remains `ncvet_audit` — **out of E3 MVP** |
| `regulator_export` bulk | No | Reserved; not E3 |

**Closed enum:** any emit must pass `assert_known` and sit in `_REGISTERED_KINDS`. No free-text `query_kind`. E3 only **promotes** the two reserved kinds into `_REGISTERED_KINDS`.

**Payload of a catalog read (metadata only):** identity (`tenant_id`, `case_id`, `version`), `content_hash`, `assessment_mode`, `published_at` / `retired_at`, `published_by` / retire fields as appropriate. **Not** `envelope_json`, **not** draft `blueprint_json`, **not** registry snapshot.

### Q2 — Scope taxonomy (split, not one `ledger:read`)

One lumped `ledger:read` would let a catalog lister also pull retirement `reason_note` (clinical_error vs operator_request). That is the same class of mistake as letting `authoring:approve` satisfy summative GRANT.

| Scope | Query kind | What it unlocks |
|-------|------------|-----------------|
| `authoring:read_published` | `get_published_case_versions` | List/get **metadata** for the caller’s tenant. No envelope. |
| `authoring:read_retirement_history` | `get_retirement_history` | Retirement audit rows for the caller’s tenant. |
| Existing `ncvet_audit` | full blueprint / trail replay | **Not E3.** |

Prefix stays `authoring:` (ConsentScope / `ConsentGrantStore`), not a new `ledger:read:*` namespace. Grants are already tenant-scoped there.

`authoring:approve` / `approve_summative` do **not** imply read scopes. Write qualification ≠ read qualification. (Publishers who need catalog access get an explicit read grant — same pattern as distinct-principal on write.)

### Q3 — Audit emission shape

| Field | Value |
|-------|--------|
| Table | `ledger_read_audit` via existing `AuditEvent` / `SqlAuditSink` |
| Cadence | **One row per HTTP call / public method call** — not sampled |
| Zero-row | `outcome=ok`, `result_row_count=0` (existing CHECK already allows 0; NULL count only on non-ok) |
| Scope denied | `outcome=scope_denied` — **still emitted**; query must not run |
| Timing | **In-band**: emit in `_audited` `finally` on the same request, after the query (or after deny), **before** the HTTP body is returned. Not a queue, not fire-and-forget. |
| Sink failure | Per-kind policy (pinned E3.a/E3.b): **`get_published_case_versions` fail-open** (UI availability); **`get_retirement_history` fail-closed** (unaudited compliance read is worse than no read). Mechanism: `_FAIL_CLOSED_KINDS` in `catalog_audit.py`. Sink failure on fail-closed path → **5xx, empty body, no retirement data**. |

**Actor vs subject (schema gap to land in E3.a):** today `subject_pseudo_id` is the *queried* subject (learner / `*` for aggregates). Catalog reads have no learner. Pin:

- Add `actor_subject_id` on `ledger_read_audit` / `AuditEvent` (nullable for pre-E3 rows; **required** on new emits).
- Populate from `AuthContext.subject_pseudo_id` for **all** ledger-read emits going forward (including existing live kinds — one column, one rule).
- Catalog queries: `subject_pseudo_id='*'` (no learner), `actor_subject_id=<caller>`.

**`caller_kind` CHECK** today is `preceptor|analyst|service`. Pin adding `authoring` so catalog readers are not laundered as `service`.

Query params remain hashed (never raw filters in the audit row). `result_row_count` is the page/result size returned, not the unpaged table cardinality.

### Q4 — Tenant isolation (defense in depth)

| Layer | Rule |
|-------|------|
| Scope | Grant is tenant-scoped in `ConsentGrantStore` (existing). Missing grant → deny + audit, no query. |
| Auth | `AuthContext.tenant_id` is the only tenant the request may name. |
| Query | SQL `WHERE tenant_id = :auth.tenant_id`. Caller-supplied `tenant_id` if present **must equal** auth tenant; mismatch → `scope_denied` (not a filtered empty list — empty would look like “no publishes”). |
| Response | Never return a row whose `tenant_id` ≠ auth tenant (belt). |

Same discipline as E2.1 A7, now on the read path.

### Q5 — Cross-tenant read

**Does not exist in E3.** No super-admin catalog. No shared-catalog bypass here (harness §3.3 sharing is a write/re-approve path, not a read scope). A regulator-wide view is `ncvet_audit` / `regulator_export`, later, with their own grants.

### Q6 — Rate / volume / bulk

| Pin | Detail |
|-----|--------|
| Bulk-export query kind | **Not in E3.** Looping pages is the only “bulk.” |
| Pagination | **Cursor-based** (not offset). Stable order: `(retired_at DESC, id DESC)` for retirement history; published catalog uses same pattern in E3.b.2. Cap page size **100** (clamp 1–100). Cursor opaque to client (base64 composite; client cannot forge cross-tenant scan). |
| Audit granularity | **One `ledger_read_audit` row per page fetch**, not per logical query. Include `page_cursor` (or `page_ordinal`) in hashed query params so auditors can distinguish spot-check vs full-history exfiltration. |
| Cadence | On-demand HTTP. No reconciler-style periodic catalog scrape. |

### Q7 — Read-your-writes

**Immediate.** These queries read `published_case_versions` / `retired_case_versions` after authoring `commit()`. They do **not** read the corpus. A publish HTTP 200 then `get_published_case_versions` in the next request **must** see the row. If it does not, that is an E3 bug, not “wait for E2.3.”

(Session create / random sample may still lag corpus — different surface, already pinned.)

### Existing Phase D GET `/v1/authoring/published*` (must not remain a silent bypass)

Those routes today authenticate (`AuthContext`) and filter by `auth.tenant_id` but **do not** require a read scope and **do not** emit `ledger_read_audit`. Leaving them beside E3 would be the hole an auditor finds.

**Pin:** E3 re-homes them through `AuditingLedgerReadService` (same query kinds + scopes). Paths may stay under `/v1/authoring/published*` as thin HTTP aliases **or** move to `/v1/ledger/...` per I3 — implementation choice in E3.a, but **no unaudited twin** after E3.a for `get_published_case_versions`. Retirement history has no Phase D list route today; E3.b adds the audited one only.

### Invariants (proposed I-E3-1…8)

| ID | Invariant |
|----|-----------|
| **I-E3-1** | Query kinds are a closed enum (`KNOWN_QUERY_KINDS` ∩ `_REGISTERED_KINDS`). Unknown kind cannot emit. |
| **I-E3-2** | Scope check **precedes** query execution. Deny audits with `outcome=scope_denied` and `result_row_count` NULL. |
| **I-E3-3** | Tenant boundary at **scope layer and query layer**; auth/query tenant mismatch is deny, not empty. |
| **I-E3-4** | Audit emit is **in-band** on the same request (not a queue). Sink failure does not mask the read (existing sink policy). |
| **I-E3-5** | Zero-row success still audits (`result_row_count=0`). Distinct from deny/error. |
| **I-E3-6** | Read-your-writes on authoring SoT: post-commit publish/retire visible without corpus heal. Query sites must not read `runtime.published_case_corpus`. |
| **I-E3-7** | Pagination mandatory on list kinds: cursor not offset; cap 100; **each page = one audit row** with cursor in params hash. |
| **I-E3-8** | No cross-tenant catalog read. No unaudited published GET twin after E3.a (308 only). |
| **I-E3-9** | Fail-closed catalog audit failure → **503** JSON envelope: `error=audit_unavailable`, `correlation_id`, optional `retry_after_seconds`. No partial rows, no cursor in body. Same shape on any paginated page. |

### Slice breakdown

| Sub | Deliverable | Acceptance |
|-----|-------------|------------|
| **E3.a** | Scopes + `actor_subject_id` + `get_published_case_versions` + legacy 308 re-home | **Closed** — `test_authoring_e3_a.py` (19 related green) |
| **E3.b.1** | `get_retirement_history` end-to-end; fail-closed sink proof; scope-split 4-cell matrix | **Closed** — `test_authoring_e3_b1.py` |
| **E3.b.2** | Cursor pagination on both catalog list kinds; per-page audit; cap 100; I-E3-9 envelope | **Closed** — `test_authoring_e3_b2.py` (32 E3-related green) |
| **E3.c** | Catalog read deny/ok observability metrics on `/metrics` | **Closed** — c.a + c.b |

Order: **a → b.1 → b.2 → c**. Full blueprint / `ncvet_audit` stays out.

**Deploy:** migration 011 before E3.a+ code — [`011_ledger_read_audit_actor_checklist.md`](../migrations/011_ledger_read_audit_actor_checklist.md).

### E3.b slice plan (pinned)

#### E3.b.1 — Retirement history + scope split

**Read path**

- `AuthoringCatalogReadService.get_retirement_history` → `retired_case_versions` directly (I-E3-6; comment at query site).
- `AuditingAuthoringCatalogReadService` promotes `get_retirement_history` into `_REGISTERED_KINDS`.
- HTTP: `GET /v1/ledger/retirement_history` (list; optional `case_id` filter).
- Response shape: `RetiredCaseVersionView` (includes `reason_code`, `retired_by` — higher privilege than published metadata).

**Fail-closed proof (pin 1)**

- `_FAIL_CLOSED_KINDS` already contains `get_retirement_history`; E3.b.1 wires the handler and proves the wire is live.
- Test with `_FailingAuditSink`: assert **status is 5xx** (not 2xx), **response body contains no retirement rows** (not empty-list masquerade), and **no partial leak** if the handler started assembling results before sink failure.
- Distinction: read failure vs sink failure — sink failure on success path still must not return data.

**Scope-split four-cell matrix (pin 2)**

| Grant | `GET …/published_case_versions` | `GET …/retirement_history` |
|-------|--------------------------------|----------------------------|
| `read_published` only | 200 | 403 + audit `consent_scope` |
| `read_retirement_history` only | 403 + audit | 200 |
| both | 200 | 200 |
| neither | 403 + audit | 403 + audit |

Each cell is its own test; union case is not a substitute for single-scope cases.

#### E3.b.2 — Cursor pagination + per-page audit

**Pagination (pin 3)**

- Cursor on stable key: `(retired_at, id)` for retirement; `(published_at, id)` for published list.
- Opaque cursor: base64url-encoded composite; server validates tenant + ordering on decode.
- `limit` default 100, max 100, min 1; `limit=101` → 400.
- No offset parameter — offset paginators lie under concurrent retire/publish.

**Per-page audit (pin 4)**

- One `ledger_read_audit` row per page HTTP call; `result_row_count` = rows in **this** page.
- `cursor` (and `limit`) included in `query_params_hash` so page 1 vs page 47 are distinguishable in audit queries.
- Exfiltration pattern (47 pages in 60s) visible as 47 audit rows, not one row with `count=4700`.

**Tests:** unpaginated list variant must not exist; fetch page 1 + page 2 with cursor → two audit rows with distinct param hashes.

#### Deploy runbook (pin 5)

- Documented in [`011_ledger_read_audit_actor_checklist.md`](../migrations/011_ledger_read_audit_actor_checklist.md): **migration first, verify column, then deploy code**.

### Explicit non-goals (E3)

- E2.5 corpus `load_probe_case`
- Corpus as the catalog SoT
- Cross-tenant reads
- Sampling audit (every call emits)
- Fail-closed **`get_retirement_history`** on sink failure ( **`get_published_case_versions`** stays fail-open )
- New `authoring_audit` columns for reads

### Open pins (closed at E3.a / E3.b plan)

1. **HTTP path:** canonical `/v1/ledger/...`; legacy `/v1/authoring/published*` → 308. ✅
2. **`authoring:approve` → `read_published`:** **no**. ✅
3. **`actor_subject_id`:** new column; nullable legacy; no backfill. ✅
4. **Sink policy:** fail-open published; fail-closed retirement. ✅

---

## E3.c — Catalog read observability (**closed**)

**Goal:** Make E3 catalog audit rows visible on the existing `/metrics` scrape surface so ops can distinguish authorization probes, tenant probes, cursor tampering, successful empty reads, and multi-page exfil bursts — **without** aggregating unlike deny signals into one "403 rate" number.

This is **metrics only**. No new HTTP routes, no SQL dashboard, no changes to audit row schema. Counters increment at the same call sites that emit `ledger_read_audit` rows (catalog audit wrapper + gateway pre-query denials).

### Why not "deny rate" as a single series

| `error_kind` (when `outcome≠ok`) | Threat signature | Must NOT aggregate with |
|----------------------------------|------------------|-------------------------|
| `consent_scope` | Scope probing — actor lacks grant | others |
| `tenant_mismatch` | Cross-tenant boundary probe | others |
| `cursor_invalid` | Pagination token tampering | others |

500 `cursor_invalid`/min from one actor = pagination probing. 500 `consent_scope`/min = authorization probing. Different runbooks.

### Three-way `result_row_count` semantics (for ok series)

| Value | Meaning | Metric bucket |
|-------|---------|---------------|
| `NULL` | Query never ran (deny / error before read) | deny/error counters only |
| `0` | Query ran; empty page | `outcome=ok`, `result_row_count=0` |
| `>0` | Query ran; data returned | `outcome=ok`, rows in page |

Alerting on "deny rate vs empty-ok rate" requires keeping these separate — conflating NULL and 0 breaks the ratio.

### Proposed metrics (Prometheus text on `/metrics`)

Extend `services/pratibimb/audit/metrics.py` (or sibling `ledger_read/metrics.py` if cleaner) — rendered alongside existing `audit_sink_*` counters in `render_prometheus_metrics()`.

**Primary counter (pinned):**

```
ledger_read_catalog_total{
  tenant_id,
  query_kind,       # get_published_case_versions | get_retirement_history
  outcome,          # ok | scope_denied | error
  error_kind,       # "" when outcome=ok; else consent_scope | tenant_mismatch | cursor_invalid | unexpected
}
```

**Pin:** `error_kind` is a **required label facet** on every increment, not folded into `outcome`. Dashboards plot separate series per `error_kind`; never sum denies without grouping by `error_kind`.

Optional companion (ships in E3.c.a):

```
ledger_read_catalog_rows_returned_total{tenant_id, query_kind}  # inc by payload row count on outcome=ok only
```

Docstring pin: increments with rows in the response payload, after auth/redaction; not DB match count.

### Increment call sites

| Event | `outcome` | `error_kind` | Where |
|-------|-----------|--------------|-------|
| Scope deny (gateway) | `scope_denied` | `consent_scope` | `record_scope_denial` |
| Tenant mismatch (gateway) | `scope_denied` | `tenant_mismatch` | `record_tenant_denial` |
| Bad cursor (gateway) | `error` | `cursor_invalid` | `record_cursor_invalid` |
| Successful page (incl. zero-row) | `ok` | `""` | `_audited` finally after emit |
| Fail-closed sink on ok read | *(no catalog_total ok row — HTTP 503)* | — | existing `audit_sink_failure_total` |

Sink failures on fail-closed reads do **not** increment `ledger_read_catalog_total{outcome=ok}` — the read did not complete to the client. Use existing sink metrics + I-E3-9 envelope for those alerts.

### Detection patterns (documentation + test names, not PromQL in repo)

1. **Scope probe:** rising `error_kind=consent_scope` grouped by `actor_subject_id` (SQL on audit table; metric gives tenant/kind facet).
2. **Tenant probe:** rising `error_kind=tenant_mismatch` — same actor, many tenants (audit SQL); metric gives spike signal.
3. **Cursor tamper:** rising `error_kind=cursor_invalid` — pagination attack; distinct from scope noise.
4. **Exfil burst:** many `outcome=ok` rows, same `query_kind`/tenant/actor, distinct `query_params_hash` in tight window (audit SQL); per-page hashing from E3.b.2 makes this visible — metric counter alone cannot encode hash diversity; **E3.c documents the SQL/query pattern**, optional future gauge deferred.

### Cardinality guardrails

- `query_kind`: closed enum (2 live catalog kinds) — safe.
- `error_kind`: closed set `{consent_scope, tenant_mismatch, cursor_invalid, unexpected, ""}` — safe.
- `tenant_id`: same bounds as existing audit metrics (~O(10²) institutions); document bucket policy if tenant count exceeds ~1000.

### Slice plan (proposed)

| Sub | Deliverable | Acceptance |
|-----|-------------|------------|
| **E3.c.a** | `ledger_read_catalog_total` + `ledger_read_catalog_rows_returned_total`; `/metrics` render; I-E3-10 option (a) fold into `catalog_safe_emit` + grep test (b); I-E3-10…14 tests | **Closed** — 31/31 green |
| **E3.c.b** | Runbook: PromQL deny-by-`error_kind`, rows-returned rate, SQL exfil-burst | **Closed** — [`ledger_read_catalog_observability.md`](../ops/ledger_read_catalog_observability.md) |

Order: **c.a → c.b**. **No E3.d** — E3 closes here unless regulator dashboard scope is opened separately (see E3 closeout below).

### Explicit non-goals (E3.c)

- Grafana/dashboard JSON in repo
- Alertmanager rules in repo
- New audit table columns
- Sampling or aggregating audit rows at write time
- Merging `error_kind` into a generic `deny` label

### Open pins (closed for E3.c.a)

1. **Metric name:** **`ledger_read_catalog_total`**. Prefix matches `ledger_read_audit` table; do not rename without renaming the audit surface. Registration comment required.
2. **Kind scope:** **Catalog kinds only** — `query_kind` label enum validated at registration: `{get_published_case_versions, get_retirement_history}`. Learner-read kinds get a separate counter in a future slice.
3. **`rows_returned_total`:** **`ledger_read_catalog_rows_returned_total`** ships in **E3.c.a**. Labels `{tenant_id, query_kind}` only; increments on ok path only, by rows in response payload after auth/redaction.

### Proposed invariants (pin before E3.c.a code)

| ID | Invariant |
|----|-----------|
| **I-E3-10** | `ledger_read_catalog_total` and `ledger_read_catalog_rows_returned_total` increment at the **same emit site** and under the **same conditions** as the corresponding `ledger_read_audit` row. Metric/audit drift is a bug. **Enforcement:** option **(a)** — fold into `catalog_safe_emit` (metrics unreachable without emit); option **(b)** belt — grep test asserts single prod call site. |
| **I-E3-11** | `error_kind` is a required label facet on `ledger_read_catalog_total`; denies are never aggregated without `error_kind` grouping. Closed enum: `consent_scope`, `tenant_mismatch`, `cursor_invalid`, `unexpected`, `""` (ok). |
| **I-E3-12** | Fail-closed 503 paths do **not** increment catalog ok counters or `rows_returned_total`; sink health remains `audit_sink_failure_total`. |
| **I-E3-13** | `rows_returned_total` increments with **response payload row count** (post-filter/redaction), not internal DB match count. |
| **I-E3-14** | E3.c makes **no audit table schema changes** — counters only. |

### E3.c.a implementation plan (awaiting invariant pin)

**Goal:** One diff adds metrics registration, render on `/metrics`, synchronized increments at catalog audit emit sites, and tests proving per-`error_kind` series + rows-returned semantics.

#### Files to touch

| File | Change |
|------|--------|
| `audit/metrics.py` (or new `ledger_read/catalog_metrics.py`) | Register `ledger_read_catalog_total`, `ledger_read_catalog_rows_returned_total`; closed `query_kind` / `error_kind` validation; render in `render_prometheus_metrics()` |
| `ledger_read/catalog_audit.py` | Single helper `_record_catalog_metrics(event, *, rows_returned=0)` called from `_emit`, `record_scope_denial`, `record_tenant_denial`, `record_cursor_invalid` — **after** audit event construction, **before/after** sink emit matching audit row fate |
| `ledger_read/catalog_metrics.py` (new) | `record_catalog_audit_metrics(...)` — keeps metrics logic out of audit wrapper body |
| `app/main.py` | Ensure `/metrics` includes new series (via existing `render_prometheus_metrics` merge if authoring metrics already composed there) |
| `tests/test_authoring_e3_c_a.py` | Per-path counter assertions |
| `docs/design/authoring_harness_phase_e.md` | Status → E3.c.a in review when diff lands |

#### Increment matrix (I-E3-10)

| Call site | Audit row | `catalog_total` labels | `rows_returned_total` |
|-----------|-----------|------------------------|------------------------|
| `_audited` finally → emit ok | `outcome=ok`, `result_row_count=N` | `{outcome=ok, error_kind="", query_kind, tenant_id}` | `+N` (payload rows) |
| `record_scope_denial` | `scope_denied`, `consent_scope` | `{outcome=scope_denied, error_kind=consent_scope, ...}` | no inc |
| `record_tenant_denial` | `scope_denied`, `tenant_mismatch` | `{outcome=scope_denied, error_kind=tenant_mismatch, ...}` | no inc |
| `record_cursor_invalid` | `error`, `cursor_invalid` | `{outcome=error, error_kind=cursor_invalid, ...}` | no inc |
| Fail-closed sink on ok read | emit attempted, 503 to client | **no inc** (I-E3-12) | no inc |

Implementation detail: metrics increment in the **same function** that calls `sink.emit` / `catalog_safe_emit`, using the `AuditEvent` fields — not duplicated label logic at HTTP layer.

#### Registration comment (pinned)

```python
# ledger_read_catalog_total — prefix matches ledger_read_audit table;
# do not rename without renaming the audit surface.
KNOWN_CATALOG_QUERY_KINDS = frozenset({
    "get_published_case_versions",
    "get_retirement_history",
})
```

```python
# ledger_read_catalog_rows_returned_total — increments with rows in the
# response payload, after all authorization and redaction filters.
# If payload count diverges from DB match count, DB count is a separate concern.
```

#### Tests (`test_authoring_e3_c_a.py`)

1. **I-E3-10 sync:** scope deny HTTP → one audit row + one `catalog_total` increment with matching `error_kind`; counts equal.
2. **Per `error_kind`:** reuse b.1/b.2 fixtures — `consent_scope`, `tenant_mismatch`, `cursor_invalid` each produce distinct metric label set (scrape `/metrics` text or test helper reading counter registry).
3. **Ok zero-row:** empty page → `{outcome=ok, error_kind=""}` + `rows_returned_total` unchanged (0 inc).
4. **Ok with data:** published list with rows → `rows_returned_total` += len(items).
5. **I-E3-12 fail-closed:** mid-page 503 test from b.2 → `catalog_total` ok series not incremented for failed page; `audit_sink_failure_total` still increments (existing).
6. **I-E3-13:** payload vs DB count — redaction simulation asserts `rows_returned_total += (N-K)`, not `+= N`.
7. **I-E3-14:** no migration files in diff (lint/review checklist item).

#### E3.c.b (closed — runbook landed)

Doc-only runbook: [`docs/ops/ledger_read_catalog_observability.md`](../ops/ledger_read_catalog_observability.md)

- PromQL: deny rate by `error_kind` (three separate alert templates)
- PromQL: `rate(ledger_read_catalog_rows_returned_total[5m])` per tenant
- SQL: exfil burst via distinct `query_params_hash` / actor / window (from E3.b.2)
- Explicit TODO for alert thresholds (out of scope)
- I-E3-10 / I-E3-13 axioms cited for runbook grounding

**Non-goals re-stated:** no Grafana JSON, no Alertmanager, **no audit schema/migration changes**, no sampling.

---

## E2.5 — Corpus-backed `load_probe_case` (**closed**)

**Boundary commit (2026-08-22):** From this slice forward, **every runtime read of published case content** — random sample, explicit pick envelope load, probe load — reads from **`runtime.published_case_corpus`**, not from `authoring.published_case_versions` / live draft JSON. Authoring tables remain lifecycle SoT; corpus is the runtime read surface. The shadow-copy era ended here.

**Goal:** Close the deferred E2.4.c promise — retire the transitional seed-only `load_probe_case` for tenant-aware runtime paths while keeping a narrow seed escape hatch for non-tenant harnesses (`version_probe`, unit tests).

**Transitional marker today:** `services/pratibimb/app/case_gen/sampler.py` — `load_probe_case(case_id)` reads `seed_corpus.json` only; comment points here.

### Problem statement

§3 pins two pools on `runtime.published_case_corpus`:

| Pool | Filter | Consumer |
|------|--------|----------|
| **Sample pool** | `tenant_id = ? AND probe_only = false` | `CaseSampler.sample_with_provenance` (random session create) |
| **Probe pool** | `tenant_id = ? AND probe_only = true` | `load_probe_case(tenant_id, case_id)` (explicit retired-case probe load) |

Random sample is corpus-backed (E2.2.e). Probe load is still seed-backed — the asymmetry is the unfinished promise.

### Load-bearing decision — audit surface

**Pin (proposed): `load_probe_case` stays out of the E3 / `ledger_read_audit` surface.**

| Surface | E2.5 probe load | E3 catalog reads |
|---------|-----------------|------------------|
| Table | `runtime.published_case_corpus` (`envelope_json`) | `authoring.published_case_versions` / `retired_case_versions` (**metadata**) |
| Path | Internal runtime (`CaseSampler` class) | HTTP `/v1/ledger/...` via `AuditingAuthoringCatalogReadService` |
| Payload | Full envelope → `CaseBlueprint` for physio / session internals | Metadata fields only — no envelope |
| Consent | None (service-internal; tenant bound at call site) | `authoring:read_published` / `authoring:read_retirement_history` |
| Audit | **No row** — same class as `CaseSampler._sample_corpus` today | One `ledger_read_audit` row per HTTP call |

**Rationale:** Probe load is a **runtime envelope read** for engine harness and (future) tenant-scoped probe sessions — not an audited catalog metadata export. Emitting `ledger_read_audit` here would conflate internal corpus reads with consent-gated HTTP catalog reads and blur the E3 boundary (“who listed the catalog” vs “which envelope bytes the runtime loaded for a probe”).

**Phase F carve-out:** Regulator-facing **audited envelope export** (`ncvet_audit` / full-blueprint read) is a **separate query kind** with its own scope, fail policy, and audit shape — not E2.5.

**E2.5 does not add:** per-probe audit rows, `authoring_audit` rows, or new E3 query kinds.

### Seed fallback × one-way flip (E2.2.e / §4)

The irreversible cutover is keyed on **`count(corpus rows for tenant) > 0`** — **any** projected row, including `probe_only=true`. Retired-only tenants flip seed off the same way published tenants do.

| Tenant state | Random sample (`probe_only=false` pool) | `load_probe_case(tenant_id, …)` |
|--------------|----------------------------------------|----------------------------------|
| **0 projected rows** + `ALLOW_SEED_FALLBACK=true` | Seed non-probe entries (§4) | Seed `probe_only` entries from `seed_corpus.json` (today’s behavior) |
| **0 projected rows** + `ALLOW_SEED_FALLBACK=false` | `EmptyCorpusError` | `ProbeCaseNotFound` / fail-closed (no silent empty blueprint) |
| **≥1 row, all `probe_only=true`** (E2.4 case #4) | `EmptyCorpusError` — pool empty, seed unreachable | **Corpus probe pool** — load matching `probe_only=true` row(s); seed unreachable |
| **≥1 row, mixed or sample-eligible** | Corpus sample pool | Corpus probe pool for probe loads; seed unreachable for both paths |

**Answer to the flip question:** yes — **probe-only projected rows count toward the one-way flip** exactly like sample-eligible rows. The flip disables **seed**, not corpus. A probe-only tenant cannot random-sample (case #4, unchanged) but **can** `load_probe_case` from corpus once E2.5 lands.

**Post-flip hard rule:** if `tenant_projected_count(tenant_id) > 0`, **`load_probe_case` never falls back to seed** — even when no corpus row matches `case_id`. Missing probe → explicit not-found error; do not silently read seed (would violate irreversible cutover).

### `probe_only` read-side semantics

| Rule | Pin |
|------|-----|
| **Derivation** | Unchanged E2.1 Pin 1 — `probe_only` derived only from `published.retired_at IS NOT NULL` at project time; never caller-supplied |
| **Probe pool filter** | `WHERE tenant_id = ? AND case_id = ? AND probe_only = true` |
| **Version selection** | If multiple retired versions share `case_id`, take **latest `published_at DESC, version DESC`** (align with corpus slot uniqueness — one row per `(tenant_id, case_id, version)`) |
| **Blueprint parse** | `case_blueprint_from_envelope_json(row.envelope_json)` — same adapter as sample pool; **never** live authoring JSON |
| **Return shape** | `CaseBlueprint` (E2.5.a); optional `load_probe_case_with_provenance` returning hash + envelope bytes is **non-goal** unless SessionManager needs it in the same slice |

### API shape (proposed)

```python
def load_probe_case(
    case_id: str,
    *,
    tenant_id: str | None = None,
    corpus_session: Session | None = None,
) -> CaseBlueprint:
    ...
```

| Call pattern | Behavior |
|--------------|----------|
| `tenant_id is None` | **Harness path only** — seed `probe_only` entries (`version_probe`, CI). No corpus session. Production callers **must** pass a real `tenant_id`. |
| `tenant_id` set + wired `corpus_session` | **Runtime path** — corpus probe pool only when projected count > 0; seed only when count == 0 and fallback allowed |
| `tenant_id` set + count > 0 + no matching row | `ProbeCaseNotFoundError` — seed unreachable (I-E25-5) |

**Harness guard (I-E25-7):** `tenant_id=None` is valid only for harness/CI entrypoints (`version_probe`, unit tests). Runtime wiring must pass tenant + corpus session. Implementation: document in docstring + regression test; optional `allow_harness_seed=True` kw-only default for harness callers if needed to make the split explicit in signatures.

**Caller updates:** `version_probe.py` stays harness path. Production/runtime callers pass `tenant_id` + corpus session (same seam as `CaseSampler`).

### Files to touch (implementation preview — after pin)

| File | Change |
|------|--------|
| `app/case_gen/sampler.py` | Corpus-backed probe load; signature + errors; remove “transitional” comment |
| `tests/test_authoring_e2_5.py` (new) | Matrix below |
| `tests/test_causal_trace.py` / `version_probe` | Confirm harness path still green with `tenant_id=None` |
| `docs/design/authoring_harness_phase_e.md` | Status → E2.5 closed when done |

### Pinned invariants (review before `sampler.py` diff)

| ID | Invariant |
|----|-----------|
| **I-E25-1** | Tenant-aware probe load reads **`probe_only=true` corpus rows only** — never the sample pool (`probe_only=false`), never live authoring JSON |
| **I-E25-2** | With **`tenant_id` set**, `load_probe_case` reads **only** `runtime.published_case_corpus` when projected count > 0; **seed access is unreachable** from this code path (no convenience fallback) |
| **I-E25-3** | **`probe_only=true` is a hardcoded SQL predicate**, not a function parameter — no callable path from `load_probe_case` to a non-probe corpus row (same discipline as E2.1 Pin 1 on the projector side) |
| **I-E25-4** | **One-way flip:** any projected row for tenant (including `probe_only=true`) disables seed for **both** sample and probe paths; probe-only tenants load probes from corpus, not seed |
| **I-E25-5** | Post-flip (or any time projected count > 0): missing probe `case_id` → **`ProbeCaseNotFoundError`**, fail closed — **no seed fallback** (preserves E2.2.e irreversibility) |
| **I-E25-6** | **`load_probe_case` does not write to `ledger_read_audit`** — out of E3 audit surface; Phase F owns audited envelope export (`ncvet_audit`) |
| **I-E25-7** | **`tenant_id=None` is harness/CI only** (`version_probe`, tests); production/runtime callers must pass a real tenant — seed harness is not a production fallback |
| **I-E25-8** | Envelope bytes from corpus **`envelope_json`** via `case_blueprint_from_envelope_json`; version tiebreak **`published_at DESC, version DESC`** |

### Test matrix (acceptance spec — code opens after invariant review)

| # | Test | Protects | Assertion shape |
|---|------|----------|-----------------|
| **1** | **Corpus hit** | I-E25-1, I-E25-8 | Publish → retire → project → `load_probe_case(tenant_id=…, case_id=…)` returns blueprint matching corpus `envelope_json` bytes |
| **2** | **Probe-only tenant** (E2.4 #4 extended) | I-E25-4 | Only `probe_only` rows projected → `CaseSampler` `EmptyCorpusError`; probe load succeeds for matching `case_id` |
| **3** | **Post-flip miss** | I-E25-5 | Tenant with projected rows, no matching probe `case_id` → `ProbeCaseNotFoundError`; seed loader not invoked |
| **4** | **Pre-flip seed** | I-E25-4 (zero-row branch) | Zero projected rows + `ALLOW_SEED_FALLBACK=true` → seed probe entry (parity with today) |
| **5** | **Pre-flip fail-closed** | I-E25-5 (zero-row branch) | Zero rows + fallback off → error, not empty/silent blueprint |
| **6** | **Harness path** | I-E25-7 | `tenant_id=None` → seed probe; `version_probe` / causal-trace tests unchanged |
| **7** | **No seed when tenant set + corpus wired** | I-E25-2 | Mock corpus query to raise; `load_probe_case(tenant_id=…, …)` propagates error — **does not** fall through to seed loader |
| **8** | **No `probe_only` parameter** | I-E25-3 | Signature inspection (E2.1 Pin 1 pattern): `load_probe_case` has no `probe_only` arg |
| **9** | **No audit rows** | I-E25-6 | After **N** tenant probe loads, **`SELECT count(*) FROM ledger_read_audit` == 0** (or in-memory sink equivalent) — structural table assertion, not “emit wasn’t called” |

**Gate:** invariant list + matrix reviewed → slice closed 2026-08-22.

---

## Phase E closeout (countersigned 2026-08-23)

**Phase E is closed.** Written after E2.5 landed so this doc has no stale “E2.5: deferred” row. Phase F acceptance lives in [`authoring_harness_phase_f.md`](./authoring_harness_phase_f.md) (already countersigned). Phase G ground rules are frozen at F closeout — referenced, not redefined here.

**Out of scope for this doc:** Phase F acceptance rows, Phase G seven questions, strategy-brief items (RCP grader digest, verifier independence, etc.).

---

### Acceptance matrix

Rule: a row is **accepted** only if it cites a green suite (or doc-only deliverable with named artifact) **and** pinned invariants / pins. Otherwise it belongs in **Deferred**.

| Slice | Deliverable | Suite / artifact | Count | Invariants / pins covered |
|-------|-------------|------------------|-------|---------------------------|
| **E1** | `blueprint_content_hash` stamp at create; finalize drift → `BLUEPRINT_HASH_DRIFT`; `EvidenceEventView.blueprint_content_hash` write-time; all-or-none pick → `INVALID_CASE_PICK` | `test_ledger_blueprint_hash.py`, `test_session_blueprint_stamp.py`, `test_evidence_blueprint_hash.py` | **19** | **I1** (hash field, create snapshot, finalize verify, NULL legacy/seed, immutability); migration `008` |
| **E2.1** | `runtime.published_case_corpus` + `CorpusProjector.project_one` / `refresh_now`; `probe_only`/`workflow_state` derived from `retired_at` | `test_corpus_projector_e2_1.py` | **13** | **E2.1 Pin 1–2** (derived `probe_only`; byte-idempotent re-project); migration `009` |
| **E2.2** | Post-commit façade; reconcile idempotency; `blueprint_source`; `project_on_miss`; SessionManager published pick | `test_corpus_facade_e2_2a.py`, `test_corpus_facade_e2_2b.py`, `test_project_on_miss_e2_2d.py`, `test_session_e2_2e.py`, `test_ledger_blueprint_source.py` | **36** | **I-E22-1…7**; migration `010` |
| **E2.3** | Startup + periodic reconciler; lag / projection metrics | `test_corpus_reconciler_e2_3a.py`, `test_corpus_reconciler_e2_3b.py`, `test_corpus_reconciler_e2_3c.py` | **27** | E2.3 job/metric pins (`CORPUS_RECONCILE_*` env clamps); `projection_stale` / reconciler counters |
| **E2.4** | Six-case provenance matrix; legacy hatch metric | `test_session_e2_4a_six_case.py`, `test_session_e2_4b_hatch.py` | **7** | **I-E24-1…5** |
| **E2.5** | Corpus-backed `load_probe_case`; harness seed path preserved; out of E3 audit surface | `test_authoring_e2_5.py` | **10** | **I-E25-1…8** |
| **E3.a** | Scopes + `actor_subject_id`; `get_published_case_versions`; legacy `/v1/authoring/published*` → **308** | `test_authoring_e3_a.py` | **8** | **I-E3-1…8** (esp. 1–3, 5–6, 8); migration `011` |
| **E3.b1** | `get_retirement_history` E2E; fail-closed sink; scope split matrix | `test_authoring_e3_b1.py` | **9** | **I-E3-2, 3, 5, 6**; fail-closed kinds set |
| **E3.b2** | Cursor pagination both catalog lists; per-page audit; cap 100; I-E3-9 503 envelope | `test_authoring_e3_b2.py` | **5** | **I-E3-7, I-E3-9** |
| **E3.c.a** | `ledger_read_catalog_*` counters; `/metrics` wiring; I-E3-10…14 | `test_authoring_e3_c_a.py` | **9** | **I-E3-10…14** |
| **E3.c.b** | Catalog observability runbook (PromQL + SQL + playbooks; thresholds TBD) | [`docs/ops/ledger_read_catalog_observability.md`](../ops/ledger_read_catalog_observability.md) | **doc-only** | Cites **I-E3-10…14** |

**E3 suite aggregate:** `e3_a` + `e3_b1` + `e3_b2` + `e3_c_a` = **31** green.  
**E2.5:** **10** green. **No accepted row without a citeable suite or named doc artifact.**

---

### Deploy checklist (consolidated)

Run in order. Checklists with `docs/migrations/*` take precedence for that migration’s verify steps.

#### Migrations (Phase E + audit prereqs)

| Order | Artifact | Before what | Verify |
|-------|----------|-------------|--------|
| **1** | `006` `ledger_read_audit` table (prereq) | Any E3 / audit sink | [`006_ledger_read_audit_checklist.md`](../migrations/006_ledger_read_audit_checklist.md) |
| **2** | `007_ledger_read_audit_fingerprint.sql` | Catalog/audit emit that writes fingerprints | Adds `result_fingerprint` (NULL on deny / pre-mig). **Not an E1–E3 slice deliverable** — pre-E audit hardening; listed so deploy order has no unexplained gap between 006 and 008 |
| **3** | `008_session_blueprint_content_hash.sql` | E1 app code | Column + length CHECK present |
| **4** | `009_runtime_published_case_corpus.sql` | E2 runtime / projector | Schema `runtime` + table; Postgres `CREATE SCHEMA` |
| **5** | `010_session_blueprint_source.sql` | E2.2.c writer stamps | `blueprint_source` + I-E22-3/4 CHECKs |
| **6** | `011_ledger_read_audit_actor.sql` | **E3 app code** | [`011_ledger_read_audit_actor_checklist.md`](../migrations/011_ledger_read_audit_actor_checklist.md) — `actor_subject_id`; `ck_audit_caller_kind` includes `authoring` |

**007 disclosure:** `007` exists and is **in-scope for deploy order** (audit table prereq chain), but **out of Phase E acceptance matrix** — it predates E1 and is not an E-slice deliverable. Gap in the E-slice number sequence is not a missing E migration.

**Phase F boundary (not Phase E — deploy after E if F is in the same release train):** migrations **012 → 013 → F app code**. See F checklists. Do not run F migrations as part of “Phase E only” cutovers.

#### Namespace / SQLite

| Backend | Rule |
|---------|------|
| **Postgres (prod)** | `CREATE SCHEMA runtime` via migration / `ensure_runtime_namespace` — **never** SQLite `ATTACH` |
| **File-backed SQLite (supported test / local)** | `ledger/namespace.py` — `ATTACH` shared `<main>.runtime` so pooled connections see `runtime.*`. Not `:memory:` ATTACH across connections |
| **Non-SQLite** | Dialect guard: ATTACH path must not run |

#### Metrics / scopes / flags

| Item | Deploy note |
|------|-------------|
| **`GET /metrics`** | Confirm scrape includes these series by **exact name**: `ledger_read_catalog_total`, `ledger_read_catalog_rows_returned_total`, `audit_sink_failure_total` (shared sink health; fail-closed paths). Wired via `render_prometheus_metrics()` → catalog metrics. Corpus ops series from E2.3 also remain registered: `authoring_corpus_projection_total`, `corpus_reconciler_*`, `projection_stale` |
| **Scopes** | Register `authoring:read_published`, `authoring:read_retirement_history`. `authoring:approve` does **not** imply read scopes |
| **Query kinds** | Live: `get_published_case_versions`, `get_retirement_history` only in catalog `_REGISTERED_KINDS` |
| **`ALLOW_SEED_FALLBACK`** | **`false` in prod.** One-way flip is structural (any projected corpus row, including `probe_only=true`, disables seed) — not a second feature flag |
| **`CORPUS_RECONCILE_INTERVAL_SECONDS`** | Default 60; clamp 15–600 |
| **`CORPUS_RECONCILE_STARTUP_CONCURRENCY`** | Default 4; clamp 1–16 |
| **Legacy authoring published GET** | Expect **308** to `/v1/ledger/...` — no unaudited twin |

---

### Deferred list (named homes — no orphan TBD)

| Item | Why not in E | Lands in |
|------|--------------|----------|
| **Regulator / `ncvet_audit` read surface** | Out of E3 MVP (metadata-only catalog) | **Phase F — closed** ([`authoring_harness_phase_f.md`](./authoring_harness_phase_f.md)); not re-opened here |
| **`regulator_export` / date-range bulk** | Named exclusion from E and F | **Phase G** (scope-crossing justification required against F boundary pin) |
| **Learner-read Prometheus counters** | E3.c scoped to catalog kinds only | **Maintenance / future slice** when learner HTTP kinds need ops visibility — separate counter family registration |
| **Grafana / Alertmanager JSON in repo** | E3.c.b is PromQL/SQL text only | **Ops baseline pass** — downstream artifact after 7d scrape; not source-of-truth in repo |
| **Catalog / NCVET runbook `THRESHOLD: TBD`** | Baseline not yet scraped | **Ops baseline pass** — parallel, **non-blocking**; doc-only edit when scrape completes |
| **HTTP route for probe fetch** | E2.5 non-goal (internal runtime API) | **Not scheduled** — open only with explicit pin if product needs it |
| **`load_probe_case_with_provenance`** | E2.5 non-goal | **Not scheduled** unless SessionManager requires hash+bytes in-band |
| **Probe-load Prometheus counters** | E2.5 non-goal | **Maintenance** if ops needs probe-path visibility |
| **E3.d** | Does not exist | **Does not exist** — do not invent a placeholder slice |
| **NCVET on-call calendar URL** | Escalation surface | **F→G handoff HARD GATE** — pin wiki/URL in NCVET scope-probe playbook **before first NCVET on-call rotation opens.** **Enforcement:** if URL unset, NCVET on-call rotation does **not** start; fail-closed **503** / `audit_unavailable` holds the line; probe response does **not** improvise (documented hold-page / ticket default). See F closeout + [`ledger_read_ncvet_observability.md`](../ops/ledger_read_ncvet_observability.md) |

---

### Invariants index (survives Phase E)

Enforcement column = primary test or structural constraint. Reference this list from Phase G seven-questions when boundary questions arise.

#### E1 — I1 (no `I-E1-*` IDs; pin table **I1**)

| ID | Statement | Enforcement |
|----|-----------|-------------|
| **I1.hash** | `blueprint_content_hash` on `session_ledger`; snapshot at create from published `content_hash` | `test_ledger_blueprint_hash.py`, `test_session_blueprint_stamp.py`; mig `008` |
| **I1.finalize** | Mandatory finalize verify → `BLUEPRINT_HASH_DRIFT` on mismatch | Finalize path tests in E1 suite |
| **I1.view** | `EvidenceEventView.blueprint_content_hash` set write-time from ledger record; `content_hash` remains `replay_hash` | `test_evidence_blueprint_hash.py` |
| **I1.null** | Legacy / seed → `NULL`; never synthesize | E1 stamp tests |
| **I1.immutability** | Set once at append; never rewritten | Writer + E1 suite |

#### E2.1 — Pins

| ID | Statement | Enforcement |
|----|-----------|-------------|
| **E2.1 Pin 1** | `probe_only` / `workflow_state` derived only from `retired_at`; no caller-supplied flag on `project_one` | `test_corpus_projector_e2_1.py`; CHECK on corpus row |
| **E2.1 Pin 2** | Re-project unchanged published row is byte-idempotent on `envelope_json` | `test_corpus_projector_e2_1.py` |

#### E2.2 — I-E22-*

| ID | Statement | Enforcement |
|----|-----------|-------------|
| **I-E22-1** | Authoring commit success independent of projection outcome | `test_corpus_facade_e2_2a.py` |
| **I-E22-2** | Reconcile after successful façade emits no second “projected” success | `test_corpus_facade_e2_2b.py` |
| **I-E22-3** | `blueprint_source` write-once at append; NULL only pre-migration | mig `010`; `test_ledger_blueprint_source.py` |
| **I-E22-4** | `published` ⇒ hash NOT NULL; `seed` ⇒ hash NULL | Writer + optional DB CHECK; blueprint_source tests |
| **I-E22-5** | `project_on_miss` sole sync runtime→projector path | Call-site comment; `test_project_on_miss_e2_2d.py` |
| **I-E22-6** | D2 race: pick + empty corpus → success + row + stamp | `test_project_on_miss_e2_2d.py` / `test_session_e2_2e.py` |
| **I-E22-7** | `project_one` fail after published exists → no session; structured error; no silent seed | Session create path tests |

#### E2.4 — I-E24-*

| ID | Statement | Enforcement |
|----|-----------|-------------|
| **I-E24-1** | Seed ledger stamp `(hash=NULL, source=seed)` on wired seed path | `test_session_e2_4a_six_case.py` case 2 |
| **I-E24-2** | Six-case matrix exhaustive for create provenance under corpus wiring | Cases 1–6 same suite |
| **I-E24-3** | Legacy hatch gains no new call sites in E2.4 | Hatch comment + `test_session_e2_4b_hatch.py` |
| **I-E24-4** | Random-sample lag accepted — no project-on-miss on sampler | Design pin / matrix |
| **I-E24-5** | Case #4 uses `EmptyCorpusError` (not a new exception class) | Case 4 test |

#### E2.5 — I-E25-*

| ID | Statement | Enforcement |
|----|-----------|-------------|
| **I-E25-1** | Tenant probe load: `probe_only=true` corpus only | `test_authoring_e2_5.py` corpus-hit |
| **I-E25-2** | Tenant set + projected > 0 → corpus only; seed unreachable | No-seed-on-corpus-raise test |
| **I-E25-3** | `probe_only=true` hardcoded SQL predicate; no parameter | Signature inspection test |
| **I-E25-4** | One-way flip: any projected row disables seed for sample **and** probe | Probe-only-tenant + pre-flip seed tests |
| **I-E25-5** | Post-flip miss → `ProbeCaseNotFoundError`; no seed fallback | Post-flip miss + fail-closed tests |
| **I-E25-6** | No `ledger_read_audit` from probe load | Structural audit-count test |
| **I-E25-7** | `tenant_id=None` harness/CI only | Harness-path tests |
| **I-E25-8** | Envelope via `case_blueprint_from_envelope_json`; tiebreak `published_at DESC, version DESC` | Corpus-hit test |

#### E3 — I-E3-*

| ID | Statement | Enforcement |
|----|-----------|-------------|
| **I-E3-1** | Closed query-kind enum | `test_i_e3_1_*` in `test_authoring_e3_a.py` |
| **I-E3-2** | Scope check before query; deny → `scope_denied`, row count NULL | E3.a / E3.b1 scope tests |
| **I-E3-3** | Tenant boundary at scope + query; mismatch = deny not empty | Tenant mismatch tests |
| **I-E3-4** | In-band audit emit; sink policy per kind | Catalog audit wrapper |
| **I-E3-5** | Zero-row success still audits (`result_row_count=0`) | Zero-row tests |
| **I-E3-6** | Read-your-writes on authoring SoT; **not** corpus | Query-site comment; retire-then-list tests |
| **I-E3-7** | Cursor pagination; cap 100; one audit row per page | `test_authoring_e3_b2.py` |
| **I-E3-8** | No cross-tenant catalog; no unaudited published GET twin (308 only) | Legacy redirect tests |
| **I-E3-9** | Fail-closed → 503 `audit_unavailable` envelope; no partial page / cursor | Mid-page 503 in E3.b2 |
| **I-E3-10** | Catalog metrics ↔ audit same site/conditions | Option (a) structural: `catalog_safe_emit` in `ledger_read/catalog_audit.py`. Option (b) belt: `test_i_e3_10_single_call_site_for_record_catalog_audit_metrics` in `test_authoring_e3_c_a.py` (greps prod for sole `record_catalog_audit_metrics(` call site). Sync smoke: `test_i_e3_10_scope_deny_audit_and_counter_in_sync`. Runbook axioms |
| **I-E3-11** | `error_kind` required label; never aggregate unlike denies | `test_i_e3_11_*`; runbook PromQL 1–3 |
| **I-E3-12** | Fail-closed 503: no ok catalog counters; use `audit_sink_failure_total` | `test_i_e3_12_*` |
| **I-E3-13** | `rows_returned` = post-redaction payload count | `test_i_e3_13_*` |
| **I-E3-14** | E3.c: no audit schema/migration changes | `test_i_e3_14_*` |

**Runbook:** [`docs/ops/ledger_read_catalog_observability.md`](../ops/ledger_read_catalog_observability.md) operationalizes I-E3-10…14.

---

### What this closeout does not redefine

| Topic | Source of truth |
|-------|-----------------|
| Phase F acceptance / boundary pin | [`authoring_harness_phase_f.md`](./authoring_harness_phase_f.md) closeout |
| Phase G opening ground rules | Frozen at F closeout (boundary as inputs; named-exclusion justification; trust-ladder; seven-question pattern) |
| NCVET observability | [`ledger_read_ncvet_observability.md`](../ops/ledger_read_ncvet_observability.md) |
| Strategy brief (RCP / Apollo / kill-list) | Separate track — not engineering closeout |

### Sequencing after this closeout

| Order | Item | Status |
|-------|------|--------|
| **1** | E2.5 | ✅ closed |
| **2** | **This Phase E closeout** | ✅ **countersigned 2026-08-23** |
| **3** | Phase F | ✅ closed (separate countersign) |
| **—** | Ops baseline `THRESHOLD: TBD` fill-in | Parallel, non-blocking |
| **—** | Phase G seven-questions draft | **In review** — [`authoring_harness_phase_g.md`](./authoring_harness_phase_g.md) |

---

## E3 closeout note (superseded by Phase E closeout above)

E3.a–c landed inside Phase E; deferred candidates from the old E3-only table are restated with honest homes in **Deferred list** above (regulator surface → F closed; `regulator_export` → G; learner counters → maintenance; Grafana JSON + THRESHOLD TBD → ops baseline).
