# Ledger read NCVET observability runbook (F.c)

This document **mirrors the structure of** [`ledger_read_catalog_observability.md`](ledger_read_catalog_observability.md) (E3.c). Sections that follow the same playbook shape are parallel; **NCVET-specific differences are called out inline** below (regulator-tooling false positives, cursor-recon SQL, session-ID enumeration, `page_ordinal` in list hashes).

**Scope:** NCVET regulator reads only — `get_session_evidence`, `list_learner_sessions`.  
**Metrics surface:** `GET /metrics` on pratibimb (`render_prometheus_metrics()` includes `ledger_read_ncvet_*`).  
**Audit surface:** `ledger_read_audit` table (`caller_kind='ncvet_audit'`; one row per HTTP call / page).

---

## Axioms the alerts depend on

### I-E3-10 — NCVET counters and audit rows cannot diverge

**Enforcement (landed F.b / F.c.a): option (a) primary, option (b) belt.**

1. **Option (a):** `ncvet_safe_emit` in `services/pratibimb/ledger_read/ncvet_audit.py` is the only function that both commits an audit row (`sink.emit`) and increments NCVET counters (`record_ncvet_audit_metrics`). Fail-closed paths raise **before** the metric increment.
2. **Option (b) belt:** `test_i_e3_10_single_call_site_for_record_ncvet_audit_metrics` greps production code and asserts exactly one call site: `ncvet_audit.py:128` inside `ncvet_safe_emit`.

Module anchors:

```text
services/pratibimb/ledger_read/ncvet_metrics.py  — module docstring, I-E3-10
services/pratibimb/ledger_read/ncvet_audit.py    — ncvet_safe_emit docstring, I-E3-10 option a
```

**Operational meaning:** When `ledger_read_ncvet_total{outcome="ok"}` increments, a matching `ledger_read_audit` row with `outcome='ok'` was persisted. Exfil PromQL treats `ledger_read_ncvet_rows_returned_total` as data-leaving proxy **because** of this pairing.

### I-F-6 — fail-closed NCVET reads

`get_session_evidence` and `list_learner_sessions` return **503** / `audit_unavailable` when the primary audit sink fails. **No** ok NCVET counter increment on 503; sink health is `audit_sink_failure_total{sink="primary"}`. Response body carries **no** `items` or `next_cursor`.

### I-F-7 — `page_ordinal` (list kind only)

`page_ordinal` is **server-derived** from cursor encode/decode — hashed in `ncvet_list_audit_params()`, never accepted from client input. Makes exfil-page PromQL and SQL `#6` meaningful: each pagination page has distinct `query_params_hash` including ordinal.

### Three-way `result_row_count` (audit SQL forensics)

| Value | Meaning | Example shape |
|-------|---------|---------------|
| **> 0** | Query ran; rows serialized to client | Successful read with data |
| **0** | Query ran; empty result set | Successful list/detail with zero matches |
| **NULL** | No serialized result — deny, tampered cursor, not-found, or pre-handler reject | Scope deny, cursor_invalid, not_found; **422 validation never reaches audit** |

**Example audit rows (illustrative — not live data):**

```text
# 1) Successful read — result_row_count > 0
outcome='ok'  query_kind='list_learner_sessions'  result_row_count=42
  error_kind=NULL  caller_kind='ncvet_audit'

# 2) Successful empty — result_row_count = 0
outcome='ok'  query_kind='list_learner_sessions'  result_row_count=0
  error_kind=NULL  caller_kind='ncvet_audit'

# 3) Query never serialized — result_row_count IS NULL
outcome='scope_denied'  query_kind='get_session_evidence'  result_row_count=NULL
  error_kind='consent_scope'  caller_kind='ncvet_audit'
```

SQL that filters `result_row_count IS NULL` (cursor recon, deny forensics) **must not** be confused with `result_row_count = 0` (legitimate empty ok pages).

### Framework validation silent to audit (422)

Requests rejected by FastAPI validation (e.g. `limit=101` on list) **never** reach the NCVET gateway. **No** `ledger_read_audit` row and **no** `ledger_read_ncvet_total` increment. Verified: `test_limit_101_returns_422_without_audit_or_metrics`.

### Other invariants (alert cross-refs)

| ID | Protects |
|----|----------|
| **I-E3-11** | Deny signals split by `error_kind`; never aggregate unlike probes into one "403 rate". |
| **I-E3-12** | Fail-closed 503 paths do not increment ok NCVET counters. |
| **I-F-1** | Reads are projection-only (`runtime.session_evidence_projection`). |

---

## Counters reference

| Counter | Labels | When it increments |
|---------|--------|-------------------|
| `ledger_read_ncvet_total` | `tenant_id`, `query_kind`, `outcome`, `error_kind` | Every persisted NCVET audit row (deny + ok). Not on fail-closed 503. Not on 422. |
| `ledger_read_ncvet_rows_returned_total` | `tenant_id`, `query_kind` | Ok path only; by payload row count returned. Zero-row ok increments `ncvet_total` only. |
| `audit_sink_failure_total` | `sink`, `tenant_id` | Shared with catalog — primary sink emit failure before 503. |
| `audit_sink_emit_total` | `sink`, `outcome`, `tenant_id` | Successful emit to primary or fallback. |

`query_kind` values: `get_session_evidence`, `list_learner_sessions`.  
`error_kind` values: `""` (ok), `consent_scope`, `tenant_mismatch`, `cursor_invalid`, `not_found`, `unexpected`.

---

## PromQL queries (first-class artifacts)

Replace `$tenant`, `$window`, and threshold placeholders before deploying to Alertmanager/Grafana.

### 1. Scope-probe deny rate — **I-E3-11**

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_ncvet_total{
    outcome="scope_denied",
    error_kind="consent_scope"
  }[$window])
)
```

**THRESHOLD:** TBD after **7 days** baseline — `ledger_read_ncvet_total{error_kind="consent_scope"}`, **`$window = 5m`**, alert when rate exceeds **`<TBD>` req/s per tenant**.

**NCVET note:** Regulator tooling (`caller_kind=ncvet_audit`) should rarely scope-deny in steady state; sustained rate warrants SQL #8 drill-down.

---

### 2. Tenant-boundary probe deny rate — **I-E3-11**

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_ncvet_total{
    outcome="scope_denied",
    error_kind="tenant_mismatch"
  }[$window])
)
```

**THRESHOLD:** TBD — `error_kind="tenant_mismatch"`, **`$window = 5m`**, **`<TBD>` req/s per tenant**.

---

### 3. Cursor-tamper rate — **I-E3-11** (NCVET-specific signal)

`outcome="error"` — includes cross-learner cursor replay (`400`, not empty page). On NCVET surface, **any** sustained `cursor_invalid` rate is high-signal (regulator clients should not tamper cursors in normal operation).

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_ncvet_total{
    outcome="error",
    error_kind="cursor_invalid"
  }[$window])
)
```

**THRESHOLD:** TBD — **`$window = 5m`**, **`<TBD>` req/s per tenant** — **intentionally tighter than catalog PromQL #3.** NCVET callers are regulator tooling; hand-crafted cursors are near-zero baseline. Do **not** normalize this threshold against catalog without reviewing I-E3-11 caller-class assumptions.

Companion SQL: `#9` (`result_row_count IS NULL`).

---

### 4. Exfil burst — rows-returned rate — **I-E3-10**

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_ncvet_rows_returned_total[$window])
)
```

**THRESHOLD:** TBD — **`$window = 5m`**, **`<TBD>` rows/s per tenant per query_kind**.

Companion — ok **page** rate (pagination exfil; I-F-7):

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_ncvet_total{
    outcome="ok",
    error_kind=""
  }[$window])
)
```

**THRESHOLD:** TBD — **`$window = 5m`**, **`<TBD>` pages/s per tenant per query_kind**.

---

### 5. Exfil burst — distinct pages (SQL #6) — **I-E3-10, I-F-7**

See SQL section — catches slow-drip session enumeration under rate thresholds.

---

### 6. Audit sink failure rate (fail-closed NCVET reads) — **I-E3-12, I-F-6**

```promql
sum by (tenant_id) (
  rate(audit_sink_failure_total{sink="primary"}[$window])
)
```

**THRESHOLD:** TBD — **`$window = 5m`**, **`<TBD>` failures/s per tenant**.

Shared counter with catalog: **one sink outage → one alert.** The metric label tells you the sink failed; filter `ledger_read_audit` by `caller_kind` (`ncvet_audit` vs catalog) for the surface breakdown of which reads were fail-closed as a result.

Both NCVET kinds are fail-closed: 503 / `audit_unavailable`, no ok NCVET counter increment.

---

## SQL sketches — slow-drip / recon (audit table)

Prometheus catches spikes; SQL catches accumulation. Requires auditor read on `ledger_read_audit`.

Per-page hashing (F.b): each `list_learner_sessions` page has distinct `query_params_hash` (includes cursor + **`page_ordinal`**).

### 6. Distinct-page exfil — actor + tenant + kind — **I-E3-10, I-F-7**

```sql
-- Parameters: :tenant_id, :query_kind, :window_interval (e.g. '4 hours')
-- NCVET-specific: catches regulator token walking all sessions in small pages.
SELECT
    actor_subject_id,
    tenant_id,
    query_kind,
    COUNT(*) AS ok_page_fetches,
    COUNT(DISTINCT query_params_hash) AS distinct_page_hashes,
    SUM(result_row_count) AS total_rows_returned
FROM ledger_read_audit
WHERE caller_kind = 'ncvet_audit'
  AND query_kind = :query_kind   -- 'list_learner_sessions' | 'get_session_evidence'
  AND tenant_id = :tenant_id
  AND outcome = 'ok'
  AND at_utc >= NOW() - :window_interval::interval
  AND actor_subject_id IS NOT NULL
GROUP BY actor_subject_id, tenant_id, query_kind
HAVING COUNT(DISTINCT query_params_hash) >= :min_distinct_pages;  -- THRESHOLD: TBD
```

**THRESHOLD:** TBD — **`COUNT(DISTINCT query_params_hash)`**, **`:window_interval = 4 hours`**, **`>= <TBD>`** distinct pages per actor.

---

### 7. Slow-drip cross-tenant probe — **I-E3-11**

```sql
SELECT
    actor_subject_id,
    COUNT(DISTINCT tenant_id) AS tenants_probed,
    COUNT(*) FILTER (WHERE error_kind = 'tenant_mismatch') AS tenant_mismatch_denies
FROM ledger_read_audit
WHERE caller_kind = 'ncvet_audit'
  AND query_kind IN ('get_session_evidence', 'list_learner_sessions')
  AND actor_subject_id = :actor_subject_id
  AND at_utc >= NOW() - :window_interval::interval
GROUP BY actor_subject_id
HAVING COUNT(DISTINCT tenant_id) >= :min_tenants;  -- THRESHOLD: TBD
```

**THRESHOLD:** TBD — **`:window_interval = 24 hours`**, **`>= <TBD>`** tenants.

---

### 8. Scope-probe actor drill-down — **I-E3-11**

```sql
SELECT
    actor_subject_id,
    tenant_id,
    query_kind,
    COUNT(*) AS consent_scope_denies
FROM ledger_read_audit
WHERE caller_kind = 'ncvet_audit'
  AND outcome = 'scope_denied'
  AND error_kind = 'consent_scope'
  AND at_utc >= NOW() - :window_interval::interval
GROUP BY actor_subject_id, tenant_id, query_kind
HAVING COUNT(*) >= :min_denies;  -- THRESHOLD: TBD
```

**THRESHOLD:** TBD — **`:window_interval = 1 hour`**, **`>= <TBD>`** denies per actor.

---

### 9. Cursor reconnaissance — **NCVET-specific**

Distinguishes tampered cursors from legitimate empty pages (`outcome='ok'`, `result_row_count=0`).

```sql
SELECT
    actor_subject_id,
    tenant_id,
    query_kind,
    COUNT(*) AS cursor_invalid_errors,
    COUNT(DISTINCT query_params_hash) AS distinct_tamper_hashes
FROM ledger_read_audit
WHERE caller_kind = 'ncvet_audit'
  AND outcome = 'error'
  AND error_kind = 'cursor_invalid'
  AND result_row_count IS NULL
  AND at_utc >= NOW() - :window_interval::interval
GROUP BY actor_subject_id, tenant_id, query_kind
HAVING COUNT(*) >= :min_cursor_errors;  -- THRESHOLD: TBD
```

**THRESHOLD:** TBD — **`:window_interval = 1 hour`**, **`>= <TBD>`** events per actor.

---

### 10. Session-ID enumeration probe — **NCVET-specific** (disabled by default — policy, not TODO)

**Policy:** Disabled by default. Enable after an **N-day** baseline confirms the steady-state `not_found` rate is compatible with alerting (legitimate regulator asks about retired/missing sessions will produce some noise). This is **not** deferred work — ship the query text now; ops turns the alert on when baseline says the FP rate is tolerable.

Burst of `not_found` on `get_session_evidence` from one actor = session-ID probing.

```sql
SELECT
    actor_subject_id,
    tenant_id,
    COUNT(*) AS not_found_errors,
    COUNT(DISTINCT query_params_hash) AS distinct_session_probes
FROM ledger_read_audit
WHERE caller_kind = 'ncvet_audit'
  AND query_kind = 'get_session_evidence'
  AND outcome = 'error'
  AND error_kind = 'not_found'
  AND result_row_count IS NULL
  AND at_utc >= NOW() - :window_interval::interval
GROUP BY actor_subject_id, tenant_id
HAVING COUNT(*) >= :min_not_found;  -- THRESHOLD: TBD
```

**THRESHOLD:** TBD — **`:window_interval = 1 hour`**, **`>= <TBD>`** not_found events per actor.

---

## Response playbook (3am on-call)

Four beats: **What fired → Check first → False-positive shape → Page vs ticket.**

### Alert: Scope probe (`consent_scope` deny rate) — **I-E3-11**

**What fired:** Rising `ledger_read_ncvet_total{error_kind="consent_scope"}`. **Check first:** SQL #8; verify grants for `ncvet:read_session_evidence` / `ncvet:read_learner_sessions`. **False-positive shape:** **Regulator-tooling deploy wave** — new NCVET client onboarding misconfigured scopes during initial integration; check the **regulator-onboarding calendar** (**URL/wiki anchor: TBD — HARD GATE / tracked blocking item: pin before first NCVET on-call rotation opens; if unset, NCVET on-call does not start; if a probe fires while TBD remains, do not improvise — ticket known-client grant fixes, hold page until calendar check is possible**) before paging. Bulk consent backfill in progress. **Page vs ticket:** Page if sustained and actors unrecognized; ticket if known regulator client needs grant fix.

---

### Alert: Tenant boundary probe (`tenant_mismatch`) — **I-E3-11**

**What fired:** Rising `ledger_read_ncvet_total{error_kind="tenant_mismatch"}`. **Check first:** SQL #7. **False-positive shape:** Regulator integration sending wrong `tenant_id` query param vs JWT tenant during config churn. **Page vs ticket:** Page if one actor hits many tenants; ticket for single misconfiguration.

---

### Alert: Cursor tamper (`cursor_invalid`) — **I-E3-11**

**What fired:** Rising `ledger_read_ncvet_total{outcome="error",error_kind="cursor_invalid"}`. **Check first:** SQL #9 (`result_row_count IS NULL`); inspect access logs for cross-learner cursor replay. **False-positive shape:** Client bug retrying with corrupted cursor; rare on regulator tooling. **Page vs ticket:** Page on NCVET surface for any sustained burst — higher baseline signal than catalog.

---

### Alert: Exfil burst — rows-returned rate — **I-E3-10**

**What fired:** High `rate(ledger_read_ncvet_rows_returned_total[5m])`. **Check first:** SQL #6 distinct hashes; correlate with page rate (PromQL #4 companion). **False-positive shape:** Authorized regulator bulk evidence pull after audit request; scheduled compliance export. **Page vs ticket:** Page if distinct pages exceed threshold and actor not on known regulator allowlist.

---

### Alert: Exfil burst — distinct pages (SQL #6) — **I-E3-10, I-F-7**

**What fired:** Slow-drip many distinct `query_params_hash` under rate threshold. **Check first:** Time spread of pages; `page_ordinal` progression in raw audit params. **False-positive shape:** Full learner session history pull for one learner (legitimate). **Page vs ticket:** Page when pattern spans many learners or tenants.

---

### Alert: Session-ID enumeration (SQL #10 — disabled by default)

**What fired:** Burst of `not_found` on `get_session_evidence` (alert only if ops enabled after baseline). **Check first:** SQL #10; distinct `query_params_hash` per actor. **False-positive shape:** Client iterating known-missing sessions after data migration gap; regulator review of a retired session ID. **Page vs ticket:** Page if probe rate exceeds TBD and actor is not a known migration script.

---

### Alert: Audit sink failure (primary) — **I-E3-12, I-F-6**

**What fired:** Rising `audit_sink_failure_total{sink="primary"}`. NCVET fail-closed → 503 without ok counter. **Check first:** DB connectivity; migration deploy order (012/013 before F code); `audit_fallback_drain_lag_seconds`. Filter audit by `caller_kind='ncvet_audit'` to see NCVET impact. **False-positive shape:** Brief failover blip. **Page vs ticket:** Page if sustained OR users report NCVET 503s; ticket for transient blip.

---

## Threshold edit checklist

| Alert | Counter / predicate | Window | Section |
|-------|---------------------|--------|---------|
| Scope probe | `ledger_read_ncvet_total{error_kind="consent_scope"}` | 5m | PromQL #1 |
| Tenant probe | `ledger_read_ncvet_total{error_kind="tenant_mismatch"}` | 5m | PromQL #2 |
| Cursor tamper | `ledger_read_ncvet_total{error_kind="cursor_invalid"}` | 5m | PromQL #3 |
| Exfil rows | `ledger_read_ncvet_rows_returned_total` | 5m | PromQL #4 |
| Exfil pages | `ledger_read_ncvet_total{outcome="ok"}` | 5m | PromQL #4 companion |
| Sink failure | `audit_sink_failure_total{sink="primary"}` | 5m | PromQL #6 |
| Distinct pages SQL | `COUNT(DISTINCT query_params_hash)` | 4h | SQL #6 |
| Cross-tenant SQL | `COUNT(DISTINCT tenant_id)` | 24h | SQL #7 |
| Scope actor SQL | `COUNT(*)` consent_scope | 1h | SQL #8 |
| Cursor recon SQL | `cursor_invalid` + `NULL` row count | 1h | SQL #9 |
| Not-found probe SQL | `not_found` count | 1h | SQL #10 |

Do **not** raise thresholds without checking the protecting invariant.

---

## Non-goals (F.c)

- Grafana dashboard JSON in repo
- Alertmanager rule files in repo
- Audit schema / migration changes
- Numeric thresholds (deferred until ops baseline)
- New query kinds or HTTP routes

---

## Related design

- [`docs/design/authoring_harness_phase_f.md`](../design/authoring_harness_phase_f.md) — Phase F scope, I-F-1…7
- [`docs/ops/ledger_read_catalog_observability.md`](ledger_read_catalog_observability.md) — parallel template (E3.c)
- [`docs/migrations/012_ncvet_session_evidence_projection_checklist.md`](../migrations/012_ncvet_session_evidence_projection_checklist.md)
- [`docs/migrations/013_ncvet_learner_sessions_keyset_index_checklist.md`](../migrations/013_ncvet_learner_sessions_keyset_index_checklist.md)
