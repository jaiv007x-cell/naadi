# Ledger read catalog observability runbook (E3.c)

**Scope:** Authoring catalog reads only — `get_published_case_versions`, `get_retirement_history`.  
**Metrics surface:** `GET /metrics` on pratibimb (rendered via `render_prometheus_metrics()`).  
**Audit surface:** `ledger_read_audit` table (append-only; one row per HTTP page fetch).

---

## Axioms the alerts depend on

### I-E3-10 — catalog counters and audit rows cannot diverge

**Enforcement (landed in E3.c.a): option (a) primary, option (b) belt.**

1. **Option (a):** `catalog_safe_emit` in `services/pratibimb/ledger_read/catalog_audit.py` is the only function that both commits an audit row (`sink.emit`) and increments catalog counters (`record_catalog_audit_metrics`). Fail-closed paths raise before the metric increment; fail-open paths increment only after emit or fallback succeeds.
2. **Option (b) belt:** `test_i_e3_10_single_call_site_for_record_catalog_audit_metrics` greps production code and asserts exactly one call site: `catalog_audit.py` inside `catalog_safe_emit`.

Module anchor (runbook citation):

```text
services/pratibimb/ledger_read/catalog_metrics.py  — module docstring, I-E3-10
services/pratibimb/ledger_read/catalog_audit.py    — catalog_safe_emit docstring, I-E3-10 option a
```

**Operational meaning:** When `ledger_read_catalog_total{outcome="ok"}` increments, a matching `ledger_read_audit` row with `outcome='ok'` was persisted (or fail-open fallback accepted). The exfil-burst PromQL below treats `rows_returned_total` rate as a proxy for data leaving the service **because** of this pairing — not because the counter is independently trustworthy.

### I-E3-13 — what `rows_returned` counts

**Semantic anchor (landed in E3.c.a):**

```text
services/pratibimb/ledger_read/catalog_audit.py — _audited finally block:
  payload_row_count = ...  # post-redaction; see I-E3-13
```

`ledger_read_catalog_rows_returned_total` increments by **payload row count after authorization and redaction** — what the client received — not `result_row_count` (DB match count before redaction). Exfil-burst rate alerts use this counter; do not substitute `result_row_count` from audit SQL when comparing to PromQL.

### Other invariants (alert cross-refs)

| ID | Protects |
|----|----------|
| **I-E3-11** | Deny signals stay split by `error_kind`; never aggregate unlike probes into one "403 rate". |
| **I-E3-12** | Fail-closed 503 paths do not increment ok catalog counters; sink health is `audit_sink_failure_total`. |
| **I-E3-14** | No schema changes in E3.c — alerts read existing audit columns only. |

---

## Counters reference

| Counter | Labels | When it increments |
|---------|--------|-------------------|
| `ledger_read_catalog_total` | `tenant_id`, `query_kind`, `outcome`, `error_kind` | Every persisted catalog audit row (deny + ok). Not incremented on fail-closed 503. |
| `ledger_read_catalog_rows_returned_total` | `tenant_id`, `query_kind` | Ok path only; by post-redaction payload rows (I-E3-13). Zero-row ok pages increment `catalog_total` only. |
| `audit_sink_failure_total` | `sink`, `tenant_id` | Primary (or fallback) sink emit failure. Fail-closed catalog reads increment `{sink="primary"}` before 503. |
| `audit_sink_emit_total` | `sink`, `outcome`, `tenant_id` | Successful emit to primary or fallback. |

`query_kind` values: `get_published_case_versions`, `get_retirement_history`.  
`error_kind` values: `""` (ok), `consent_scope`, `tenant_mismatch`, `cursor_invalid`, `unexpected`.

---

## PromQL queries (first-class artifacts)

Replace `$tenant`, `$window`, and threshold placeholders before deploying to Alertmanager/Grafana.

### 1. Scope-probe deny rate — **I-E3-11**

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_catalog_total{
    outcome="scope_denied",
    error_kind="consent_scope"
  }[$window])
)
```

**THRESHOLD:** TBD after **7 days** baseline — counter `ledger_read_catalog_total{error_kind="consent_scope"}`, window **`$window = 5m`**, alert when rate exceeds **`<TBD>` req/s per tenant**.

---

### 2. Tenant-boundary probe deny rate — **I-E3-11**

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_catalog_total{
    outcome="scope_denied",
    error_kind="tenant_mismatch"
  }[$window])
)
```

**THRESHOLD:** TBD after **7 days** baseline — counter `ledger_read_catalog_total{error_kind="tenant_mismatch"}`, window **`$window = 5m`**, alert when rate exceeds **`<TBD>` req/s per tenant**.

---

### 3. Cursor-tamper rate — **I-E3-11**

Note: `outcome="error"` (not `scope_denied`) — top-level dashboard split without drilling into `error_kind`.

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_catalog_total{
    outcome="error",
    error_kind="cursor_invalid"
  }[$window])
)
```

**THRESHOLD:** TBD after **7 days** baseline — counter `ledger_read_catalog_total{outcome="error",error_kind="cursor_invalid"}`, window **`$window = 5m`**, alert when rate exceeds **`<TBD>` req/s per tenant**.

---

### 4. Exfil burst — rows-returned rate (metric) — **I-E3-10, I-E3-13**

Per-tenant + query_kind. The counter has no `actor_subject_id` label; actor-level drill-down uses the SQL sketch below.

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_catalog_rows_returned_total[$window])
)
```

**THRESHOLD:** TBD after **7 days** baseline — counter `ledger_read_catalog_rows_returned_total`, window **`$window = 5m`**, alert when rate exceeds **`<TBD>` rows/s per tenant per query_kind**.

Companion — ok **page** rate (detects pagination-driven exfil even when pages are small):

```promql
sum by (tenant_id, query_kind) (
  rate(ledger_read_catalog_total{
    outcome="ok",
    error_kind=""
  }[$window])
)
```

**THRESHOLD:** TBD after **7 days** baseline — counter `ledger_read_catalog_total{outcome="ok"}`, window **`$window = 5m`**, alert when page rate exceeds **`<TBD>` pages/s per tenant per query_kind**.

---

### 5. Audit sink failure rate (fail-closed catalog reads) — **I-E3-12**

```promql
sum by (tenant_id) (
  rate(audit_sink_failure_total{sink="primary"}[$window])
)
```

**THRESHOLD:** TBD after **7 days** baseline — counter `audit_sink_failure_total{sink="primary"}`, window **`$window = 5m`**, alert when rate exceeds **`<TBD>` failures/s per tenant**.

Retirement history (`get_retirement_history`) is fail-closed: failures surface as **503** with `audit_unavailable` envelope and **no** ok catalog counter increment.

---

## SQL sketches — slow-drip exfil (audit table)

Prometheus rate alerts catch **spikes**. These queries catch **accumulation**: many distinct page fetches (`query_params_hash`) by one actor, each under the rate threshold, over a longer window. Requires read access to `ledger_read_audit` (auditor role).

Per-page cursor hashing (E3.b.2) means each pagination page has a **distinct** `query_params_hash` — full catalog walk produces many ok rows with different hashes.

### 6. Distinct-page exfil — actor + tenant + kind — **I-E3-10**

```sql
-- Parameters: :tenant_id, :query_kind, :window_interval (e.g. '1 hour')
-- Flags actors who fetched many *distinct pages* in the window.
SELECT
    actor_subject_id,
    tenant_id,
    query_kind,
    COUNT(*) AS ok_page_fetches,
    COUNT(DISTINCT query_params_hash) AS distinct_page_hashes,
    SUM(result_row_count) AS total_db_rows_returned
FROM ledger_read_audit
WHERE caller_kind = 'authoring'
  AND query_kind = :query_kind          -- 'get_published_case_versions' | 'get_retirement_history'
  AND tenant_id = :tenant_id
  AND outcome = 'ok'
  AND at_utc >= NOW() - :window_interval::interval
  AND actor_subject_id IS NOT NULL
GROUP BY actor_subject_id, tenant_id, query_kind
HAVING COUNT(DISTINCT query_params_hash) >= :min_distinct_pages;  -- THRESHOLD: TBD
```

**THRESHOLD:** TBD after **7 days** baseline — predicate `COUNT(DISTINCT query_params_hash)`, window **`:window_interval = 1 hour`**, alert when **`>= <TBD>` distinct pages** per actor per tenant per query_kind.

---

### 7. Slow-drip cross-tenant probe — **I-E3-11**

```sql
-- Parameters: :actor_subject_id, :window_interval
SELECT
    actor_subject_id,
    COUNT(DISTINCT tenant_id) AS tenants_probed,
    COUNT(*) FILTER (WHERE error_kind = 'tenant_mismatch') AS tenant_mismatch_denies
FROM ledger_read_audit
WHERE caller_kind = 'authoring'
  AND query_kind IN ('get_published_case_versions', 'get_retirement_history')
  AND actor_subject_id = :actor_subject_id
  AND at_utc >= NOW() - :window_interval::interval
GROUP BY actor_subject_id
HAVING COUNT(DISTINCT tenant_id) >= :min_tenants;  -- THRESHOLD: TBD
```

**THRESHOLD:** TBD after **7 days** baseline — predicate `COUNT(DISTINCT tenant_id)`, window **`:window_interval = 24 hours`**, alert when **`>= <TBD>` tenants** touched by one actor with any `tenant_mismatch` denies.

---

### 8. Scope-probe actor drill-down (companion to PromQL #1) — **I-E3-11**

```sql
SELECT
    actor_subject_id,
    tenant_id,
    query_kind,
    COUNT(*) AS consent_scope_denies
FROM ledger_read_audit
WHERE caller_kind = 'authoring'
  AND outcome = 'scope_denied'
  AND error_kind = 'consent_scope'
  AND at_utc >= NOW() - :window_interval::interval
GROUP BY actor_subject_id, tenant_id, query_kind
HAVING COUNT(*) >= :min_denies;  -- THRESHOLD: TBD
```

**THRESHOLD:** TBD after **7 days** baseline — predicate `COUNT(*)`, window **`:window_interval = 1 hour`**, alert when **`>= <TBD>` denies** per actor per tenant.

---

## Response playbook (3am on-call)

### Alert: Scope probe (`consent_scope` deny rate) — **I-E3-11**

**What fired:** Rising `ledger_read_catalog_total{error_kind="consent_scope"}` rate for a tenant. **Check first:** Run SQL #8 for top `actor_subject_id` values; verify whether the actor is a known integration missing a newly required scope (`authoring:read_published` or `authoring:read_retirement_history`). **False-positive shape:** Single actor after a deploy that tightened scope checks; bulk consent backfill in progress. **Page vs ticket:** Page if rate is sustained (>2 evaluation periods) and actors are unrecognized service accounts; ticket if one known client needs scope grant remediation.

---

### Alert: Tenant boundary probe (`tenant_mismatch` deny rate) — **I-E3-11**

**What fired:** Rising `ledger_read_catalog_total{error_kind="tenant_mismatch"}` — caller JWT tenant does not match requested `tenant_id`. **Check first:** SQL #7 for the actor; inspect recent requests for wrong `tenant_id` query param vs token. **False-positive shape:** Misconfigured CI fixture hardcoding wrong tenant; client SDK bug in multi-tenant routing. **Page vs ticket:** Page if one actor hits many tenants in SQL #7; ticket for single-tenant misconfiguration.

---

### Alert: Cursor tamper (`cursor_invalid` rate) — **I-E3-11**

**What fired:** Rising `ledger_read_catalog_total{outcome="error",error_kind="cursor_invalid"}`. **Check first:** Audit rows for affected `request_id` / `actor_subject_id`; look for truncated or hand-edited cursors in access logs. **False-positive shape:** Client retrying with an expired cursor after long idle; mobile app caching stale `next_cursor`. **Page vs ticket:** Page if burst from one actor (>TBD/min); ticket for isolated client bug.

---

### Alert: Exfil burst — rows-returned rate — **I-E3-10, I-E3-13**

**What fired:** High `rate(ledger_read_catalog_rows_returned_total[5m])` for a tenant. **Check first:** Confirm counter semantics — post-redaction payload rows (`catalog_audit.py`, `# post-redaction; see I-E3-13`), not DB match count. Run SQL #6 for distinct `query_params_hash` per actor. Correlate with ok page rate (PromQL #4 companion). **False-positive shape:** Legitimate bulk export by authorized catalog sync job; full pagination through large retirement history after policy change. **Page vs ticket:** Page if distinct page hashes (SQL #6) exceed threshold AND actor is not on known export allowlist; ticket for approved bulk job over threshold (tune threshold, do not disable alert).

---

### Alert: Exfil burst — distinct pages (SQL #6) — **I-E3-10**

**What fired:** Slow-drip pattern — many distinct pagination pages under PromQL rate threshold. **Check first:** Same actor/tenant/kind grouping; inspect `query_params_hash` diversity and time spread in raw audit rows. **False-positive shape:** Search indexer walking full catalog once per day. **Page vs ticket:** Page when distinct pages in 1h exceed TBD and pattern is new for that actor; ticket for scheduled indexer (add to allowlist documentation).

---

### Alert: Audit sink failure (primary) — **I-E3-12**

**What fired:** Rising `audit_sink_failure_total{sink="primary"}`. Catalog fail-closed reads return **503** / `audit_unavailable` without ok counter increment. **Check first:** DB connectivity to audit table; recent migration deploy order (011 before E3 code); fallback drain lag gauge `audit_fallback_drain_lag_seconds`. **False-positive shape:** Brief blip during DB failover; single retry succeeds. **Page vs ticket:** Page if sustained >2 periods OR retirement_history 503s reported by users; ticket for transient blip with successful fallback drain.

---

## Threshold edit checklist

When baseline period completes, edit **only** the lines marked `THRESHOLD: TBD` in this file:

| Alert | Counter / predicate | Window | File section |
|-------|---------------------|--------|--------------|
| Scope probe | `ledger_read_catalog_total{error_kind="consent_scope"}` | 5m | PromQL #1 |
| Tenant probe | `ledger_read_catalog_total{error_kind="tenant_mismatch"}` | 5m | PromQL #2 |
| Cursor tamper | `ledger_read_catalog_total{error_kind="cursor_invalid"}` | 5m | PromQL #3 |
| Exfil rows rate | `ledger_read_catalog_rows_returned_total` | 5m | PromQL #4 |
| Exfil page rate | `ledger_read_catalog_total{outcome="ok"}` | 5m | PromQL #4 companion |
| Sink failure | `audit_sink_failure_total{sink="primary"}` | 5m | PromQL #5 |
| Distinct pages SQL | `COUNT(DISTINCT query_params_hash)` | 1h | SQL #6 |
| Cross-tenant SQL | `COUNT(DISTINCT tenant_id)` | 24h | SQL #7 |
| Scope actor SQL | `COUNT(*)` consent_scope denies | 1h | SQL #8 |

Do **not** raise thresholds to silence alerts without checking the protecting invariant — see cross-refs above.

---

## Non-goals (E3.c.b)

- Grafana dashboard JSON in repo
- Alertmanager rule files in repo
- Audit schema / migration changes
- Numeric thresholds (explicitly deferred until baseline exists)

---

## Related design

- [`docs/design/authoring_harness_phase_e.md`](../design/authoring_harness_phase_e.md) — E3.c scope, invariants I-E3-10…14
- [`docs/migrations/011_ledger_read_audit_actor_checklist.md`](../migrations/011_ledger_read_audit_actor_checklist.md) — `actor_subject_id` column for SQL drill-down
