# Audit Sink Failure Policy

## Principle

Audit emission failure must never mask the primary read outcome. But
persistent audit sink failure is itself a compliance event and must be
detected, escalated, and recorded out-of-band.

This policy defines the three regimes — transient, degraded, and
critical — and the system's obligations in each.

## Regimes

### 1. Transient failure (default, tolerated)

**Definition:** A single `AuditSink.emit()` call raises. The wrapped
read has already produced its result (or its exception).

**System behavior:**
- The primary read outcome is returned to the caller unchanged.
- The sink exception is caught in `_safe_emit` and logged at ERROR
  level with structured fields: `query_id`, `query_kind`, `tenant_id`,
  `sink_error`.
- The `audit_sink_failure_total` counter increments (tenant-scoped).
- A record is written to the **fallback sink** (see §4).

**Rationale:** A read-service outage caused by an audit DB blip is a
worse outcome than a missing audit row, provided the missing row is
itself recorded elsewhere.

### 2. Degraded (rate-based, alerting)

**Definition:** `audit_sink_failure_total` exceeds **5 failures per
minute per tenant**, OR the ratio
`audit_sink_failure_total / audit_sink_emit_total` exceeds **1%** over
a 5-minute window.

**System behavior:**
- Prometheus alert `AuditSinkDegraded` fires to on-call.
- The read service continues to serve queries.
- Fallback sink continues to capture missed events.
- An incident ticket is auto-opened with severity S2.

**Rationale:** Persistent low-rate failure is almost always a schema
drift, credential rotation issue, or disk-pressure event. It is
recoverable without service impact, but it is *not* silently
tolerable.

### 3. Critical (fail-closed for summative)

**Definition:** Fallback sink itself is unavailable, OR
`audit_sink_failure_total` exceeds **50 failures per minute per
tenant**, OR audit DB has been unreachable for > 5 minutes continuous.

**System behavior:**
- Read service enters **summative-restricted mode**:
  - Queries with `caller_kind ∈ {regulator, insurer_aggregate,
    ncvet_audit}` are rejected with HTTP 503 and
    `Retry-After` header.
  - Formative queries (learner self-view, preceptor review) continue
    to serve.
- Prometheus alert `AuditSinkCritical` fires to on-call, severity S1.
- The mode transition is itself recorded as a system event to the
  fallback sink AND the application log AND paged out-of-band.

**Rationale:** For summative-grade consumers, an unaudited query is
worse than a delayed query. The regulator's contract is "every query
you served is provably logged." If we cannot uphold that, we serve
fewer queries, not more silent ones.

## §4. Fallback sink

The fallback sink is a **local append-only JSONL file** at
`/var/lib/pratibimb/audit-fallback/{tenant_id}/{YYYY-MM-DD}.jsonl`,
fsync'd per line, rotated daily, and monitored for size.

Contents are the same `AuditEvent` canonical JSON that would have gone
to `SqlAuditSink`, plus a `fallback_reason` field capturing the
primary sink's exception class.

A separate cron (`audit-fallback-drain.py`, ships with the service)
replays fallback JSONL into the audit DB every 60 seconds when the
primary sink is healthy, and deletes each JSONL line only after
successful DB insert (verified by `query_id` uniqueness check).

The fallback sink is deliberately **not** a queue or Kafka topic. A
file on the same disk as the service is the simplest thing that
survives a DB outage without introducing a second distributed system
into the audit critical path.

## §5. Auditor-visible guarantees

The following invariants are testable and documented for external
audit:

1. **No served query is unlogged.** Every response returned to a
   caller has a corresponding audit row, either in the primary sink
   or in the fallback sink pending drain.
2. **No unlogged query is served in summative mode.** When both
   primary and fallback are unavailable, summative queries are
   refused.
3. **Fallback drain is idempotent.** `query_id` is unique across
   primary and fallback; re-drain of a partially-drained file cannot
   create duplicates.
4. **Sink transitions are themselves audited.** Every entry into and
   exit from degraded or critical mode is recorded with timestamp,
   trigger metric value, and duration.

## §6. Operational runbook reference

See `runbooks/audit_sink_incident.md` for on-call response steps.
Summary: check audit DB connectivity, check fallback disk free,
inspect `audit-fallback-drain.log`, confirm mode transition events
match alert timeline.
