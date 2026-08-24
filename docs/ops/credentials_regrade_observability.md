# Credentials + regrade + ARP observability runbook (H.c + I.c)

**Scope:** G credential surfaces + H regrade mint/verify + I ARP mint/verify (`surface="arp"` after I.c cutover).  
**Companion:** [`ledger_read_ncvet_observability.md`](ledger_read_ncvet_observability.md) (F.c), catalog observability (E3.c), [`authoring_harness_phase_i.md`](../design/authoring_harness_phase_i.md).

**Closed `surface` label set** (free-form forbidden; new values need a phase pin):

| `surface` | Meaning |
|-----------|---------|
| `catalog` | E3 authoring catalog fail-closed |
| `ncvet` | F NCVET ledger-read fail-closed |
| `credentials` | G credentials fail-closed |
| `regrade` | H regrade mint + verify-callback fail-closed (**ARP no longer emits here after I.c**) |
| `arp` | I ARP mint + verify Shape A fail-closed (I.c+) |

**No `regrade_verifier` surface** — issuer vs verifier decomposition uses sub-labels (`sink`, `component`) if needed.

Top-line PromQL:

```text
sum by (surface) (rate(audit_sink_failure_total[5m]))
```

---

## Surface table (on-call)

| surface | PromQL (slice) | Threshold | Alert fire | On-call role | Escalation |
|---------|----------------|-----------|------------|--------------|------------|
| `catalog` | `rate(audit_sink_failure_total{surface="catalog"}[5m])` | **Provisional** — review by **2026-09-22** | **`rate > 0`** after **~2m suppression** | Authoring platform on-call | Platform lead → SRE |
| `ncvet` | `rate(audit_sink_failure_total{surface="ncvet"}[5m])` | Provisional — review by 2026-09-22 | rate > 0 / ~2m suppression | Regulator-tooling on-call | Platform lead → SRE |
| `credentials` | `rate(audit_sink_failure_total{surface="credentials"}[5m])` | Provisional — review by 2026-09-22 | rate > 0 / ~2m suppression | Authoring platform on-call | Platform lead → SRE |
| `regrade` | `rate(audit_sink_failure_total{surface="regrade"}[5m])` | Provisional — review by 2026-09-22 | rate > 0 / ~2m suppression | Authoring platform on-call | Platform lead → SRE |
| `arp` | `rate(audit_sink_failure_total{surface="arp"}[5m])` | Provisional — review by **2026-09-22** (same PR as H.c threshold review) | rate > 0 / ~2m suppression | Authoring platform on-call (`role:authoring-platform-oncall-lead`) | Platform lead → SRE |

**`arp` fail-closed semantics:** primary audit sink emit failed on ARP mint (`POST /v1/arp/session/{id}`) or ARP verify Shape A (`POST /v1/arp/verify`, `GET /v1/arp/artifacts/{arp_id}`) → client-visible **503** `audit_unavailable` (retryable). Envelope: `{"error":"audit_unavailable","correlation_id":…}` — same shape as H, but routes and audit `caller_kind` are ARP-specific (`ncvet_recompute` mint / `ncvet_arp_verifier` verify).

**`arp` triage (do not copy-paste regrade prose):** (1) confirm `rate(audit_sink_failure_total{surface="arp"}[5m])` non-zero (2) distinguish mint vs verify via recent `ledger_read_audit` / fail-closed path — mint uses `arp_audit` write path; verify uses Shape A `emit_arp_verify_audit` (3) check primary sink / ledger DB for the ARP service (4) if users report ARP 503s, check correlation_id against sink logs (5) non-503 ARP client errors (`error_kind` from I.a/I.b: `transcript_digest_mismatch`, `arp_rubric_identity_collision`, `alternate_rubric_unpublished`, `digest_mismatch` → **422**, scope deny → **403**) are **not** this alert — sink-failure alert is 503-only (6) escalate Platform lead → SRE if sink down >15m.

**Alert rationale:** audit sink failures are fail-closed — sustained non-zero rate means user-visible 503s. Threshold numerics close when ops baseline lands (review date above); do not leave as undated TODO.

### Threshold review (2026-09-22) — owner + artifact

| Field | Pin |
|-------|-----|
| **Date** | 2026-09-22 |
| **Owner (role)** | `role:authoring-platform-oncall-lead` (maps via on-call tool; not a person) |
| **Artifact** | PR against this file (`docs/ops/credentials_regrade_observability.md`) with production-signal data (PromQL snapshots / page history) |
| **Cadence** | First review within one production-signal window ending **2026-09-22** |
| **I.c coordination (deliberate)** | Same date as ARP `surface` cutover — threshold review PR **must include the `arp` row’s threshold**, not only re-review the four H-era surfaces |

Review dates without owner + artifact drift; both are pre-committed here.

**Fail-closed meaning (all five surfaces):** primary audit sink emit failed → request returns **503** `audit_unavailable` (retryable); client libraries share the same envelope across catalog / ncvet / credentials / regrade / arp.

**Immediate triage (any surface page):** (1) confirm `sum by (surface) (rate(audit_sink_failure_total[5m]))` which surface is non-zero (2) check primary sink health / DB connectivity for that path (3) if sink restored, confirm rate returns to 0 and 503s stop (4) escalate Platform lead → SRE if sink remains down >15m.

---

## Shape A — verify-assist audit

Verifier→NAADI callbacks **always** emit `ledger_read_audit` with `caller_kind=ncvet_verifier` (H) or `ncvet_arp_verifier` (I):

- `POST /v1/regrade/verify` → `query_kind=verify_regrade_assist`
- `GET /v1/regrade/artifacts/{regrade_id}` → `query_kind=fetch_regrade_artifact`
- `POST /v1/arp/verify` → `query_kind=verify_arp_assist`
- `GET /v1/arp/artifacts/{arp_id}` → `query_kind=fetch_arp_artifact`

**Ordering:** audit emit runs **before** the HTTP response is returned (emit-then-respond). A sink failure yields **503** with no success body.

Offline verify (no NAADI call) emits **nothing**. Sink failure on callback → **503**.

---

## Fail-closed sink-failure call sites (grep belt)

Expected production `AUDIT_SINK_FAILURE_TOTAL.labels(...).inc()` sites — seven `file:function` anchors:

| # | `file:function` | `surface` | Kind / path |
|---|-----------------|-----------|-------------|
| 1 | `ledger_read/catalog_audit.py:catalog_safe_emit` | `catalog` | catalog fail-closed |
| 2 | `ledger_read/ncvet_audit.py:ncvet_safe_emit` | `ncvet` | NCVET fail-closed |
| 3 | `credentials/status_service.py:CredentialStatusService.fetch_status_list` | `credentials` | status-list fetch |
| 4 | `regrade/service.py:RegradeService._write_audit` | `regrade` | H mint |
| 5 | `regrade/verify_audit.py:emit_regrade_verify_audit` | `regrade` | H Shape A |
| 6 | `arp/service.py:ArpService._write_audit` | `arp` | I ARP mint (I.c+) |
| 7 | `arp/verify_audit.py:emit_arp_verify_audit` | `arp` | I ARP verify Shape A (I.c+) |

**Registry sync:** `_EXPECTED_FAIL_CLOSED_SITES` in `test_authoring_h_c_2.py` must match this table.

---

## Deploy / rollback checklists

| Migration | Checklist |
|-----------|-----------|
| 016 regrade tables + `ncvet_regrader` | [`016_regrade_artifact_checklist.md`](../migrations/016_regrade_artifact_checklist.md) |
| 017 UNIQUE `(tenant_id, session_id, transcript_digest)` — **excludes `grader_version`**; version bump = 409 until explicit re-issue | [`017_regrade_artifact_unique_checklist.md`](../migrations/017_regrade_artifact_unique_checklist.md) |
| 019 ARP tables + `ncvet_recompute` (+ reserved `ncvet_arp_verifier`) | [`019_arp_artifact_checklist.md`](../migrations/019_arp_artifact_checklist.md) |
| 020 status-list `identifier_kind` CHECK + UNIQUE | [`020_status_list_identifier_kind_checklist.md`](../migrations/020_status_list_identifier_kind_checklist.md) |

**017 rollback:** app first, then drop UNIQUE index (see checklist). Prefer forward-fix.

**Queue deferred:** revisit numerics by 2026-09-22 or 30d after first H.a prod deploy (phase_h deferred list).

---

## Entropy

| Id | Floor |
|----|-------|
| `evidence_ref` | `EVIDENCE_REF_ENTROPY_BYTES = 32` |
| `regrade_id` | `REGRADE_ID_ENTROPY_BYTES = 32` |

---

## I.c — ARP `surface` cutover

**Status:** **I.c.1** — new ARP fail-closed emissions use `surface="arp"`.

| Field | Pin |
|-------|-----|
| **Cutover date** | **2026-09-22** (deliberate co-schedule with H.c threshold review — threshold PR includes `arp` row) |
| **Deploy identifier** | **tag=`v2.14.0`** · **sha=`f1fe486fe9fb048ac982e0a4d4b49ae293cb9797`** (I.c.1 cutover deploy) |
| **Grafana annotation text** | `arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)` at deploy timestamp — **not** bare timestamp |
| **Historical invariant** | Pre-cutover `audit_sink_failure_total{surface="regrade"}` samples from ARP paths are **not rewritten**. Discontinuity is intentional: pre-I.c ARP used shared regrade surface; I.c introduced dedicated `arp` surface. Rewriting metric labels post-hoc would corrupt the forensic record. |
| **Post-cutover ARP query** | `rate(audit_sink_failure_total{surface="arp"}[5m])` |
| **H regrade (unchanged)** | `rate(audit_sink_failure_total{surface="regrade"}[5m])` — H mint/verify only after cutover |
| **Spanning cutover (union-regex)** | Surface-only union is **unsafe** (pulls H regrade). Use surface union **plus** caller_kind: span queries that need “all ARP fail-closed in window” must filter ARP kinds. Example PromQL for metric rate still uses `surface=~"regrade|arp"` with Grafana cutover annotation; for **audit-row** forensics across the cutover: `caller_kind=~"ncvet_recompute|ncvet_arp_verifier"` (ARP) vs `ncvet_regrader` (H). Safe historical ARP window query intent: `surface=~"regrade|arp" AND caller_kind=~"ncvet_recompute|ncvet_arp_verifier"` |
| **Pre-cutover ARP disambiguation** | Under `surface="regrade"`, ARP vs H: `caller_kind ∈ {ncvet_recompute, ncvet_arp_verifier}` (ARP) vs `caller_kind=ncvet_regrader` (H). Metric series alone cannot split pre-cutover ARP from H; `caller_kind` is the discriminator that makes the union-regex safe. |
| **Ship checklist** | [`i_c_ship_checklist.md`](i_c_ship_checklist.md) — cutover-only diff; exact `arp-surface-cutover@…` |
| **Compat delete** | [`i_c_2_compat_delete_checklist.md`](i_c_2_compat_delete_checklist.md) — separate deploy; `status-list-v2-only@…` |

### Compat coercion delete (I.c.2 C2–C4) — landed, not optional

**Two Grafana annotations (do not conflate):**

| Annotation text template | Event | Starts C3 clock? |
|--------------------------|-------|------------------|
| `arp-surface-cutover@<tag> (sha:<sha>)` | I.c.1 ARP `surface` relabel | **No** |
| `status-list-v2-only@<tag> (sha:<sha>)` | v1 status-list readers retired (v2-only) | **Yes — sole C3 clock-start** |

Surface relabel and v2-only **may share a calendar day** but are **independent deploys** (or independent commits). C3 must **not** use `arp-surface-cutover@…` as clock-start — that marks metric-label cutover, not “no v1 reader can coerce.” Accidental zero during v1/v2 coexistence does **not** start the 30 days.

| C3 field | Pin |
|----------|-----|
| **Clock-start** | Timestamp of Grafana annotation `status-list-v2-only@<tag> (sha:<sha>)` |
| **Window** | 30 consecutive days at zero **after** that annotation |
| **Clock reset** | Any non-zero coercion after clock-start **resets** the window: next true-zero day = new day-0. **Window-bounded:** stale day-30 PR from pre-reset window must close/rebase — not mergeable because calendar hit day 30 |
| **Owner (role)** | `role:authoring-platform-oncall-lead` |
| **C4 alert (landed)** | `increase(status_list_compat_coercion_total[1d]) > 0` after v2-only → ticket to same owner. Routes: authoring-platform on-call. |
| **Day-30 PR target files** | (1) `services/pratibimb/credentials/status_list.py` — remove `normalize_status_list_entries` v1 missing-kind → `credential` coercion branch (2) `services/pratibimb/audit/metrics.py` — remove or dead-code `STATUS_LIST_COMPAT_COERCION_TOTAL` if unused (3) tests that assert coercion increment |
| **Day-30 PR description** | Must cite **I.c.2 C3**, link `status-list-v2-only@…` annotation, and state observed window (e.g. “zero from YYYY-MM-DD to YYYY-MM-DD; C4 did not fire”) |
| **Day-30 open gate** | No C4 fire during the 30 consecutive zero days |

**C4 not deferred** — without the alert, observation falls to manual daily check by the owner; that trade-off is rejected for I.c.2. Alert is the landed observation artifact.
