# Authoring harness — Phase I (alternate-rubric recompute)

Status: **Q1–Q7 locked.** **I.a–I.c.2 closed (docs).** Ship fill-in pending cutover deploy.
Parent: [`authoring_harness.md`](./authoring_harness.md) — Appendix C (trust ladder + post-ladder).  
Prior: [`authoring_harness_phase_h.md`](./authoring_harness_phase_h.md) (**closed**), [`authoring_harness_phase_g.md`](./authoring_harness_phase_g.md) (**G.a + G.b closed**), [`authoring_harness_phase_f.md`](./authoring_harness_phase_f.md) (**closed**).

**Review pattern:** seven questions → pins → invariants → matrix-as-acceptance-spec → code against green matrix.  
**CI order:** `pytest -m h_acceptance` (**44/44**, fail-fast) → then `pytest -m i_acceptance`.

---

## Glossary

| Term | Expansion / meaning |
|------|---------------------|
| **RCP** | **Replayable Competency Proof** — trust-ladder rung 3 (Phase H). |
| **ARP** | **Alternate Rubric Proof** — Phase I portable artifact under authorized alternate rubric *R₂* vs sealed transcript *T*. |
| **Post-ladder** | Beside F→G→H — **not** rung 4. |
| **`arp_id`** | Opaque ARP artifact id (disjoint id space from `regrade_id` / `evidence_ref` / `credential_id`) |
| **`arp_audit` / `arp_artifacts`** | Dedicated ARP tables (distinct from `regrade_*`) |
| **`ncvet_recompute`** | Mint-side `caller_kind` (migration **019**; 018 = samvaad_verifier) |
| **`ncvet_arp_verifier`** | Verify-side `caller_kind` — **distinct** from `ncvet_verifier`; emitters in I.b; CHECK reserved in 019 |

---

## What Phase I is

Post-ladder **score-under-different-rubric** against H's named exclusion. Does not amend H sealed RCP / UNIQUE / F kinds / G issue-revoke.

---

## Prelude ground rules (frozen)

1. Rung inheritance — frozen H/G/F inputs; must-not-touch H UNIQUE / F kinds / G contracts.  
2. `i_acceptance` reserved; `h_acceptance` frozen.  
3. H deferred: score-under-different-rubric **in-scope**; async + grade-pending **deferred further**; codegen **maintenance**.

---

## Frozen inputs (H + trust surface)

| Constraint | Value |
|------------|-------|
| H HTTP / mint / verify | `/v1/regrade/`; `ncvet_regrader` / `ncvet_verifier` Shape A |
| Transcript | `runtime.session_transcript_projection` only |
| H UNIQUE | `(tenant, session, transcript)` — `grader_version` excluded |
| Keys (I-H-14 option b) | Distinct G / H / **I** (`arp_issuer_key_id`) on shared status-list |
| Status-list entries | **`identifier_kind`** ∈ `{credential, regrade, arp}` + id; UNIQUE `(kind, id)` tenant-scoped (I.b) |

**Boundary:** *I produces score-bound ARPs under authorized published rubrics against the same sealed transcript projection; it does not amend H RCP bytes, does not write `regrade_*`, and does not widen F kinds.*

---

## Seven questions (Q1–Q7 locked)

### Q1 — distinct operation (six properties)

Scopes `ncvet:recompute_alternate_rubric` + `ncvet:verify_arp`; `/v1/arp/`; `arp_id` + `arp_*` tables; projection + digest-before-grade; coexistence without UPDATE of H RCP; signed portable ARP; fail-closed 503; distinct `arp_issuer_key_id`.

**Fail-closed belt (I.a):** sixth sink-failure site `arp/service.py:_write_audit` registered under surface=`regrade` until I.c may split — four-part landing (registry + runbook + existing `regrade` PromQL + same 2026-09-22 threshold owner).

### Q2 — score-bound + eligibility + three-field audit

| Pin | Detail |
|-----|--------|
| Output | Score-bound MVP; **full trace = deliberate named exclusion I-I-15** (not TODO) |
| Eligibility | Recompute-eligible + **published at grader invocation**; un-publish between request and grader → fail-closed |
| **Transactional eligibility** | Eligibility re-asserted in same write transaction as `arp_audit`/`arp_artifacts` commit — not check-then-gap-then-invoke. Mid-flight un-publish → no artifact (`error_kind=alternate_rubric_unpublished`) |
| Audit forensics | **`alternate_rubric_id`** + **`authority_id`** + **`transcript_rubric_schema_version`** |
| Identity collision | **`alternate_rubric_id` ≠ sealed rubric id** → `error_kind=arp_rubric_identity_collision`, fail-closed **pre-grader** (**I-I-16**) |

### Q3 — named exclusions

Bulk · cross-session · async · full trace · H-UNIQUE amendment · caller_kind creep beyond `ncvet_recompute`/`ncvet_arp_verifier` · F/G Holds.

### Q4 — verify independence

Offline authoritative; assist ≡ offline; Shape A; keys via G.b list; no parallel ARP revocation; status-list typed by `identifier_kind`; staleness inherits G.b.

### Q5 — binding + coexistence (I-I-5 both halves)

Optional ARP→RCP/credential cites only.  
**I-I-5:** (A) ARP mint → zero new `regrade_audit` rows; (B) ARP mint → zero UPDATE/DELETE on `regrade_artifacts` via **SQLAlchemy event listener** (not query-log); (C) RCP byte-identical after ARP; **inverse** H regrade mint → zero `arp_*` writes.

### Q6 — isolation + UNIQUE

**I-6a…f = scope zero-implication** (each its own test; **I-6f** lands assertion pre-route, tightens to **403** in I.b):

| # | Assertion | Slice |
|---|-----------|-------|
| **I-6a** | `ncvet:recompute_alternate_rubric` alone ↛ H sealed regrade mint | I.a |
| **I-6b** | `ncvet:verify_arp` alone ↛ H verify/fetch (`ncvet:verify_regrade`) | I.a |
| **I-6c** | `ncvet:regrade_session` alone ↛ ARP recompute mint | I.a |
| **I-6d** | `ncvet:verify_regrade` alone ↛ ARP recompute mint (verify route deferred) | I.a |
| **I-6e** | All four scopes held → distinct audit trails; no `caller_kind` cross-contamination | I.a |
| **I-6f** | **`ncvet:recompute_alternate_rubric` alone ↛ `ncvet:verify_arp`** verify assist + artifact fetch — I.b matrix tightens pre-route placeholder to **403** | I.b |

**Pair symmetry:** I-6b (verify_arp ↛ H) + **I-6f** (recompute ↛ ARP verify) are the cross-surface zero-implication pair for the two ARP scopes.
**Forensic axes (not I-6; map elsewhere):** ARP caller_kind closed set → I-I-10/I-6e; zero writes to `regrade_*`/`credential_ledger` → I-I-5; status-list kind isolation → I-I-14; grader uses alternate rubric → I-I-2/I-I-16; distinct key material → I-I-12.

UNIQUE ARP: `(tenant_id, session_id, transcript_digest, alternate_rubric_id)`.

### Q7 — version tuple (structured fields; `artifact_schema_version=v1`; additive v1.x)

`transcript_digest` · `transcript_rubric_schema_version` · `sealed_rubric_id` · `alternate_rubric_id` · `alternate_rubric_version` · `authority_id` · `grader_version` · `artifact_schema_version` · `digest_alg_id` (= inherited `sha256-canonical-json-v0`, no second default).

---

## Invariants I-I-1…16

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **I-I-1** | Opaque `arp_id` only in portable envelope | Grep |
| **I-I-2** | Eligibility at grader invoke; **transactional** with write | Cases 15, 15b |
| **I-I-3** | Scope zero implication — I-6a…f | Isolation module |
| **I-I-4** | Projection-only transcript | Greps |
| **I-I-5** | Symmetric coexistence (row-count **and** UPDATE-count both named) | Cases 17–19 + listener |
| **I-I-6** | Fail-closed audit → no artifact | Case 8 |
| **I-I-7** | Sync MVP | Case 14 |
| **I-I-8** | Q3 exclusions | Doc + greps |
| **I-I-9** | Digest-before-grade | Case 4 |
| **I-I-10** | `ncvet_recompute` + three forensics fields | Cases 3, 9, 10 |
| **I-I-11** | Grading isolation | Case 12 |
| **I-I-12** | Distinct `arp_issuer_key_id` | Case 16 |
| **I-I-13** | Four-column UNIQUE | 019 + race |
| **I-I-14** | Status-list `identifier_kind` + UNIQUE (kind, id) | I.b |
| **I-I-15** | Full trace **deliberately** excluded — chosen not-to-build, not TODO | Allowlist |
| **I-I-16** | `alternate_rubric_id ≠ sealed_rubric_id` | Case 16b |

---

## I.a — closed (countersigned 2026-08-23)

| # | Contingent |
|---|------------|
| **C1** | Rubric identity collision matrix case — `arp_rubric_identity_collision` pre-grader |
| **C2** | Eligibility transactional with grader/write; mid-invoke un-publish race case |
| **C3** | Coexistence UPDATE half = SQLAlchemy listener, not query-log |
| **C4** | I-6a…f scope zero-implication (I-6f pinned; I.b tightens) |
| **C5** | Case **18** = inverse coexistence (regrade → zero ARP); case **19** = ARP → zero regrade (listener) |
| **C6** | `h_acceptance` before I matrix in CI |
| **C7** | `ncvet_recompute` in **019** (app+CHECK); `ncvet_arp_verifier` distinct from `ncvet_verifier`, CHECK-reserved in 019, emitters in I.b |

### Route

`POST /v1/arp/session/{session_id}` · scope `ncvet:recompute_alternate_rubric` · `caller_kind=ncvet_recompute`  
Order: tenant+scope → projection → digest → **identity collision** → **eligibility (transactional)** → grader → audit+artifact → signed ARP.

### Migration 019

`arp_audit` / `arp_artifacts` · four-column UNIQUE · caller_kind CHECK (`ncvet_recompute`, `ncvet_arp_verifier` reserved) · `arp_recompute_eligible_rubrics` registry · checklist (**forward-only** once any ARP row written). **Note:** 018 is Samvaad `samvaad_verifier` — do not reuse.

### Matrix (`test_authoring_i_a.py` + `test_authoring_i_a_isolation.py` — **28/28**)

| # | Test |
|---|------|
| 1–14 | Happy / portable / three-field audit / digest / scopes / tenant / fail-closed / enum / cardinality / projection / isolation / F-kinds / sync |
| **9b** | **`ncvet_arp_verifier` zero emit sites** in I.a tree (grep guard — I.b flips when first emitter lands) |
| **15** | Ineligible / unpublished at grader invoke |
| **15b** | Mid-grader un-publish race → no artifact |
| **16** | Wrong G/H key |
| **16b** | Same-as-sealed rubric → `arp_rubric_identity_collision` |
| **17** | Coexistence positive — RCP byte-identical after ARP |
| **18** | Coexistence **inverse** — H regrade mint → zero `arp_audit` / zero UPDATE `arp_artifacts` |
| **19** | Coexistence **structural** — ARP mint → zero `regrade_audit` + listener zero UPDATE `regrade_artifacts` |

**Isolation:** `test_authoring_i_a_isolation.py` — **I-6a…f** (six tests).

### I.a closed notes — carry-forwards → I.b / I.c

| # | Pin |
|---|-----|
| **1** | **`ncvet_arp_verifier` grep guard** (`test_i_a_9b`) — remove/adjust only when I.b first emitter lands same commit |
| **2** | **019 forward-only** after first ARP row — checklist explicit; no silent DELETE on rollback |
| **3** | **`surface="regrade"` → `surface="arp"`** — I.c landing item for fail-closed counter + closed-surface runbook (see §I.c) |
| **4** | **I-6f** — recompute ↛ verify_arp; I.b matrix asserts **403** on verify assist + artifact fetch |

### Non-goals (I.a)

Verify HTTP / offline / status-list schema (I.b) · full trace · bulk/async · mutating `regrade_*`

**I.a.1 countersigned closed 2026-08-23** — `h_acceptance` 44/44 → `i_acceptance` **28/28**.

---

## I.b — Verify + status-list extension (**C1–C8 countersigned; 020 landed; I.b.1 review**)

**Goal:** Portable ARP **verify / present** + G.b status-list **`identifier_kind`** extension (I-I-14). Does **not** re-open I.a mint, H RCP, or G issue/revoke semantics.

### Contingent pins (countersigned 2026-08-23 — asks folded)

| # | Pin |
|---|-----|
| **C1** | Migration **020** — table `status_list_identifier`; **DB CHECK** `identifier_kind ∈ {credential, regrade, arp}` + **PK/UNIQUE** `(tenant_id, identifier_kind, identifier_id)`; backfill revoked credentials → `credential` (non-null). Matrix: same `identifier_id` under two kinds both accepted; `bogus` → CHECK fail |
| **C2** | **`ncvet_arp_verifier`** emitters **same commit** as first verify route; flip `test_i_a_9b` → inverted “exactly one emit site” |
| **C3** | Shape A — `verify_arp_assist` + `fetch_arp_artifact`; `caller_kind=ncvet_arp_verifier`. Verify audit carries **two digests**: presented (envelope) + stored/recomputed (artifact) — H.b mint two-digest forensic discipline on the verify trail |
| **C4** | **Assist ≡ offline** — dedicated matrix case: assist accept/reject **byte-identical** to offline lib for same inputs (`test_i_b_assist_output_bytes_equal_offline_output_bytes`) |
| **C5** | No parallel ARP revoke HTTP — status-list only |
| **C6** | I-6f — **two cases**: recompute-only → **403** on **`POST /v1/arp/verify`** *and* **`GET /v1/arp/artifacts/{arp_id}`** (per-route) |
| **C7** | Staleness inherits **G.b Option A** (`max_staleness = 7d`) — explicit cross-ref: [`authoring_harness_phase_g.md`](./authoring_harness_phase_g.md) §G.b “List-fetch / staleness policy (pin 3)”. Option B per-call flag available. Do not redefine numerics in I |
| **C8** | Verify **read-only** — **structural** AST/grep denylist: verify package must not INSERT/UPDATE `arp_audit` / `arp_artifacts` (not behavioral-only) |

### Routes / surfaces

| Method | Path | Scope | `caller_kind` | Behavior |
|--------|------|-------|---------------|----------|
| `POST` | `/v1/arp/session/{session_id}` | `ncvet:recompute_alternate_rubric` | `ncvet_recompute` | **Unchanged** (I.a) |
| `POST` | `/v1/arp/verify` | **`ncvet:verify_arp`** | **`ncvet_arp_verifier`** | Assist; assist ≡ offline |
| `GET` | `/v1/arp/artifacts/{arp_id}` | **`ncvet:verify_arp`** | **`ncvet_arp_verifier`** | Tenant-bound fetch |
| `GET` | `/v1/credentials/status_list` | `ncvet:fetch_status_list` | `ncvet_verifier` | Extended — `status_list.v2` entries |
| — | Offline lib | — | — | Allowlist + sig under `arp_issuer_key_id` |

**Mint audit** = `arp_audit` + `ncvet_recompute`. **Verify audit** = Shape A `ledger_read_audit` + `ncvet_arp_verifier`.

### Status-list schema (I-I-14)

```text
status_list_schema_version: "status_list.v2"
entries: [ { identifier_kind, identifier_id, revoked_at? }, ... ]
```

| Rule | Pin |
|------|-----|
| Closed kind | App enum + **DB CHECK** on `status_list_identifier` |
| UNIQUE | `(tenant_id, identifier_kind, identifier_id)` |
| Backfill | 020 → `credential`; new publishes require kind; v1 snapshots without kind → map to `credential` for **compat window only** |
| Minimization | No `session_id` / transcript digest in entries |
| Revoke | Registry + append snapshot; **no** `/v1/arp/revoke` |

### Migration 020

Landed: [`020_status_list_identifier_kind.sql`](../../services/pratibimb/ledger/migrations/020_status_list_identifier_kind.sql) + [`020_status_list_identifier_kind_checklist.md`](../migrations/020_status_list_identifier_kind_checklist.md). ORM: `StatusListIdentifierRow`. Belt: `test_authoring_i_b_migration.py`.

---

## I.b.1 — slice plan (**countersigned 2026-08-23 — green**)

**Sequence:**

1. ✅ Land **020**; SQL belt under **`i_acceptance`**.
2. ✅ Verify route + emitters + **9b flip** (atomic slice).
3. ✅ Matrix vs I-I-* + I-6f green (`i_acceptance` **51/51**; `h_acceptance` **44/44**).
4. ⬜ Status-list schema closeout (gated behind this review).

### Twelve pre-open pins (countersigned)

| # | Pin |
|---|-----|
| **1** | `test_i_a_9b` docstring names f-string / concat / dict-lookup OOS with one-line reasons |
| **2** | 019 checklist: **flag-flip first-response**, **route-removal escalation** |
| **3** | I.c: cutover date + **historical `surface="regrade"` rows unchanged** |
| **4** | UNIQUE exact tuple = **`(tenant_id, identifier_kind, identifier_id)`** on `status_list_identifier` — **not** session/transcript/`query_kind` (019 artifact UNIQUE remains `(tenant, session, digest, alternate_rubric_id)`) |
| **5** | C3 both digests on **success** Shape A rows too (assert in case 5 / happy verify) |
| **6** | C4 byte equality on **full response body**, not verdict-only |
| **7** | C8 denylist = **transitive** import closure of verify/fetch modules |
| **8** | Compat close = **version-based** (v1 readers retired); emit **`status_list_compat_coercion_total`** per coercion |
| **9** | Case 16 = **static subset-check** on registered emit sites (not runtime mutation) |
| **10** | Case 17 = presented≠stored digest → **`422`** + `error_kind=digest_mismatch` (403 reserved for scope-deny; unknown `arp_id` fetch → **404** in case 6) |
| **11** | One-commit revert: **revert the commit**, not partial — emitters + routes + 9b land/unland atomically |
| **12** | 020 SQL belt (2) registered under **`i_acceptance`** only |

### Commit message template (pin 11)

```text
I.b.1: ARP verify/fetch + ncvet_arp_verifier emitters + inverted 9b

Atomic: routes, Shape A emitters, and 9b flip land together.
Revert plan: revert this commit entire — do not partial-revert
emitters or 9b while leaving the other.
```

### Test matrix (`test_authoring_i_b.py`) — **20 cases**

| # | Test | Protects |
|---|------|----------|
| **1** | Offline verify happy | I-I-12 |
| **2** | **`test_i_b_assist_output_bytes_equal_offline_output_bytes`** — full body bytes, accept+reject | C4 |
| **3** | Wrong / G/H key reject | I-I-12 |
| **4** | Allowlist rejects extra key | Closed envelope |
| **5** | Shape A `ncvet_arp_verifier`; **success** params carry presented+stored digests | C3 |
| **6** | Fetch happy; unknown `arp_id` → **404** | Opaque id |
| **6b** | Wrong tenant → **403/404**, no body | Cross-tenant |
| **7** | Fail-closed verify audit → 503; `surface="regrade"` | Fail-closed |
| **8a** | Recompute-only → **403** on **verify** (`ncvet:verify_arp` deny) | C6 |
| **8b** | Recompute-only → **403** on **fetch** (`ncvet:verify_arp` deny) | C6 |
| **9** | Four-way scope matrix | Q6 |
| **10** | Same `identifier_id`, kinds `regrade`+`arp` — both INSERT OK | C1 |
| **11a** | Revoke `regrade` → `arp` same id still verifies (observable) | Cross-kind |
| **11b** | Revoke `arp` → `regrade`/`credential` same id unaffected (observable) | Cross-kind |
| **12** | Status-list fetch emits `ncvet_verifier` only | G.b |
| **13** | Transitive import denylist — verify closure ↛ mint write models | C8 |
| **14** | No new F ledger kinds | Freeze |
| **15** | 020 SQL belt + `bogus` CHECK fail | C1 |
| **16** | Inverted 9b — static subset: exactly emit site(s) registered | C2 |
| **17** | Presented≠stored digest → **422** `digest_mismatch`; both digests on audit | C3 |

**CI:** `h_acceptance` 44/44 → `i_acceptance` (**28** I.a + **2** 020 belt + **20** I.b.1 = **50**).

### I.b non-goals

Mint changes · H/G routes · async · bulk · full trace · `surface="arp"` (I.c) · parallel ARP revoke HTTP

## I.b — closed (I.b.1 signed off 2026-08-23)

**Belt:** `i_acceptance` **51/51** (28 I.a + 2 020 belt + 21 I.b.1 incl. compat counter in marker).  
**CI:** `h_acceptance` 44/44 → `i_acceptance` 51/51.

Status-list schema closeout remains a separate review step before I.b formal closeout doc if needed; verify/fetch/identifier_kind MVP is green.

---

## I.c — observability split (**I.c.1 countersigned closed 2026-08-23**)

**Belt:** `h_acceptance` 44/44 → `i_acceptance` **58/58** (51 prior + 7 I.c).

**Proof-time by contingent:** C1 = ship-time (checklist) · C2 = test-time (`test_i_c_7`) · C3 = calendar-time (I.c.2 owner + observation).

Closed surface set (literal in `test_authoring_h_c_2.py`): `{catalog, ncvet, credentials, regrade, arp}`.  
Fail-closed registry: **seven** `file:function` anchors (five H-era + `arp/service.py:_write_audit` + `arp/verify_audit.py:emit_arp_verify_audit`).

### Carry-forwards → I.c.2

| # | Pin |
|---|-----|
| **1** | Ship checklist artifact: [`docs/ops/i_c_ship_checklist.md`](../ops/i_c_ship_checklist.md) — fill tag, fill SHA, Grafana annotation |
| **2** | C3 clock-start owner + observation: `role:authoring-platform-oncall-lead`; non-zero alert during 30d window; day-30 delete PR |

---

## I.c.2 — ship ops + C3 delete discipline (**countersigned 2026-08-23**)

**Goal:** C1 ship gate + C3 calendar delete non-drift. **C4 alert promoted to landed** (not optional). No further surface relabel.

### Contingents (locked)

| # | Pin |
|---|-----|
| **C1** | Ship checklist = deploy gate; path in cutover PR description; all boxes ticked before merge |
| **C2** | C3 clock-start = Grafana **`status-list-v2-only@<tag> (sha:<sha>)`** — **not** `arp-surface-cutover@…`. Accidental zero during coexistence does not start the clock. Surface relabel and v2-only are **independent** (may share a calendar day; distinct annotations) |
| **C3** | Owner `role:authoring-platform-oncall-lead`; day-30 PR targets named files; PR cites C3 + observed window; **clock resets** on any non-zero / C4 fire |
| **C4** | **Landed** (not optional): `increase(status_list_compat_coercion_total[1d]) > 0` after v2-only → same owner. Day-30 PR open **contingent on no C4 fire** in window |

### Day-30 PR target files (named — no archaeology)

1. `services/pratibimb/credentials/status_list.py` — v1 missing-kind coercion in `normalize_status_list_entries`
2. `services/pratibimb/audit/metrics.py` — `STATUS_LIST_COMPAT_COERCION_TOTAL`
3. Tests asserting coercion increment

### Artifacts

| Artifact | Path |
|----------|------|
| Surface cutover ship | [`i_c_ship_checklist.md`](../ops/i_c_ship_checklist.md) — **merged** |
| V2-only reader retirement ship | [`i_c_2_v2_only_ship_checklist.md`](../ops/i_c_2_v2_only_ship_checklist.md) — **scope open** |
| C3/C4 delete window | [`i_c_2_compat_delete_checklist.md`](../ops/i_c_2_compat_delete_checklist.md) |
| Runbook pins | [`credentials_regrade_observability.md`](../ops/credentials_regrade_observability.md) §Compat coercion delete |

---

## I.c.2.1 — v2-only reader retirement (**scope open 2026-08-24**)

**Goal:** Retire v1 status-list readers in production; drop `status-list-v2-only@…` annotation (C3 clock-start). **Not** day-30 compat-delete — shim stays until calendar window closes.

**Belt target:** `h_acceptance` 44/44 → `i_acceptance` prior green + new I.c.2.1 cases (TBD at implement).

### Product pins (v2-only deploy)

| # | Pin |
|---|-----|
| **P1** | **Publish v2 only:** `CredentialStatusService._publish_snapshot` emits `status_list.v2` with `identifier_kind` + `identifier_id` on every entry |
| **P2** | **Credential offline verify accepts v2:** `credentials/verify.py` verifies `status_list.v2` envelopes (not reject as unknown schema) |
| **P3** | **ARP verify rejects v1 / missing-kind:** `arp/verify.py` — no inline `credential_id`-only coercion; v1 schema or entry without `identifier_kind` → reject (not coerce) |
| **P4** | **Compat shim retained:** `normalize_status_list_entries` + `STATUS_LIST_COMPAT_COERCION_TOTAL` **remain in tree** until day-30 PR (counter should stay at zero post v2-only) |
| **P5** | **Historical snapshots unchanged:** pre-v2-only `envelope_bytes` in DB not rewritten |

### Ship diff scope (locked)

| In scope | Out of scope |
|----------|--------------|
| P1–P3 product changes | Delete `normalize_status_list_entries` coercion branch |
| Runbook v2-only deploy fill | `arp-surface-cutover@…` doc changes |
| `status-list-v2-only@…` annotation template | Day-30 delete PR |
| C4 alert wiring confirmation | Metric/sample rewrite |

### Review walk (three points — mirror cutover)

| # | Pin |
|---|-----|
| **1** | §I.c.2.1 vs [`i_c_2_v2_only_ship_checklist.md`](../ops/i_c_2_v2_only_ship_checklist.md) — all boxes ticked in PR description |
| **2** | Annotation exact-match: `status-list-v2-only@<tag> (sha:<sha>)` at runbook emit site |
| **3** | V2-only diff: `git show <commit> -- docs/ops/ \| rg "arp-surface-cutover@"` zero matches; **no** compat-delete removal in diff |

### Non-goals

Day-30 delete · new surfaces · ARP mint/verify behavior beyond status-list read path · bundling cutover annotation

### Non-goals

New surfaces · ARP product behavior · historical Prom rewrite · opening day-30 PR before calendar window

### Gate

**Cutover PR merged — countersign closed 2026-08-24.** Three-point walk cleared (checklist boxes in PR description · annotation exact-match · diff-grep `status-list-v2-only@` clean on `9c71dd4`).

| Field | Value |
|-------|-------|
| **tag** | `v2.14.0` |
| **cutover SHA** | `f1fe486fe9fb048ac982e0a4d4b49ae293cb9797` |
| **ship commit** | `9c71dd4` |
| **annotation (pinned)** | `arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)` |
| **i_acceptance** | **58** = 51 + 7 (incl. `test_i_c_7` C2/P8 forensic); tip **61** with +3 ship-checklist meta |

**Ops (2026-09-22) — no further review gate:** deploy `9c71dd4` on `v2.14.0` · drop Grafana annotation with pinned string · tick three post-deploy smoke boxes. Ship confirmation when smoke green, **or** open I.c.2 v2-only scope when that cutover is ready to walk. **Either order — no dependency beyond calendar.**

**Next-gate posture (whichever lands first):**
- **Ship confirmation:** exact-match Grafana drop against pinned template before ticking box 3; `rg -n "arp-surface-cutover@"` on runbook emit site (see ship checklist)
- **I.c.2 v2-only walk:** first-walk confirms cutover annotation is not C3 clock-start; v2-only diff negative-greps `arp-surface-cutover@`; C4 alert routing live to `role:authoring-platform-oncall-lead`

**Carry-forwards (unchanged):**
1. I.c.2 v2-only deploy + `status-list-v2-only@…` (sole C3 clock-start)
2. Day-30 delete PR after 30 consecutive zero days; C4 `increase(status_list_compat_coercion_total[1d]) > 0` resets window
3. Owner: `role:authoring-platform-oncall-lead`

---

## Slice plan

| Slice | Status |
|-------|--------|
| **I.a** | **Closed** — 28/28; migration 019 |
| **I.b** | **Closed** — I.b.1 signed off; **51/51** |
| **I.c.1** | **Closed** — 58/58; `surface="arp"`; cutover PR merged |
| **I.c.2** | **Countersigned** — cutover ship closed; v2-only + C3 window pending |
| **I.c.2.1** | **Scope open** — v2-only reader retirement; ship checklist drafted |

---

## Gate

**Cutover countersign closed.** Standing: ops annotation drop at **2026-09-22**, then ship-confirmation **or** I.c.2.1 v2-only walk (either order).

**I.c.2.1 scope open** — v2-only ship checklist + product pins at §I.c.2.1. Next: implement P1–P3, then v2-only PR walk.