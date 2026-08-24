# Authoring harness — Phase G (signed credentials)

Status: **Phase G closed for G.a + G.b (countersigned 2026-08-23).** G.c (observability) remains optional follow-on.  
Parent: [`authoring_harness.md`](./authoring_harness.md).  
Prior: [`authoring_harness_phase_f.md`](./authoring_harness_phase_f.md) (**closed**), [`authoring_harness_phase_e.md`](./authoring_harness_phase_e.md) (**closed**).  
Next: [`authoring_harness_phase_h.md`](./authoring_harness_phase_h.md) (**RCP — closed**).

**Review pattern:** seven questions → pins → invariants → slice plan → G.a closed → G.b closed → (G.c optional).

**Parallel track:** strategy brief (grader artifact digest) — parallel; folds into I-G-3 / I-G-7 when landed.

---

## Glossary

| Term | Expansion / meaning |
|------|---------------------|
| **RCP** | **Replayable Competency Proof** — trust-ladder rung 3 (Phase H). Holder-replayable package that supports deterministic re-grade / envelope+response replay against a verifier. **Not** “Regulator Credential Protocol.” |
| **VC** | W3C Verifiable Credential (or VC-shaped signed artifact) — rung 2 (Phase G) |
| **Rung 1** | Audit record — F closed (`ledger_read_audit` + NCVET projection reads) |
| **Rung 2** | Signed credential — G MVP |
| **Rung 3** | RCP — Phase H |
| **`evidence_ref`** | Opaque, non-guessable, per-credential-version, independently-revocable handle resolving issuer-side to an underlying session for F `get_session_evidence` |
| **`ncvet_issuer`** | `ledger_read_audit.caller_kind` for issuance-time audited F reads (closed-enum extension) |
| **`credential_ledger`** | Append-only SoT for issued credentials (issuer-internal; not F projection) |

Ladder (locked naming through H): **audit record (F) → signed credential (G) → replayable competency proof (H).**

---

## Frozen inputs (not open questions)

### F boundary pin

| Constraint | Frozen value |
|------------|--------------|
| HTTP surface (NCVET **reads**) | `/v1/ledger/` only — **no new query kinds in G** |
| Audit identity (F reads) | `caller_kind` ∈ closed enum; `actor_subject_id` required |
| Read path | Projection-only (`runtime.session_evidence_projection`) |
| Live query kinds (F) | `get_session_evidence`, `list_learner_sessions` only |
| Named exclusions | Date-range; `regulator_export`; demographics; snapshot; **bulk credential issuance**; **public `evidence_ref` status** |

### Phase boundary (G vs F)

**G issues credentials over F’s frozen evidence; G does not re-project, re-audit as SoT, or re-derive rung-1 artifacts.** Issuance **consumes** audited `get_session_evidence` (producing a normal `ledger_read_audit` row for that read with `caller_kind=ncvet_issuer`). Issuance does **not** invent a parallel evidence projection or a second audit table for evidence.

### E invariants index

[`authoring_harness_phase_e.md`](./authoring_harness_phase_e.md) §Phase E closeout — I1, E2.1 Pins, I-E22-*, I-E24-*, I-E25-*, I-E3-1…14.

### F→G HARD GATE (ops)

Calendar URL before first NCVET on-call rotation (now covers issuer + audit reads once G lands). Unset → on-call does not start; 503 holds; no improvisation.

---

## What Phase G is

| Rung | Ownership |
|------|-----------|
| 1 Audit record | **F — closed** (G consumes) |
| 2 Signed credential | **G MVP** (G.a issue → G.b verify/revoke → G.c obs) |
| 3 RCP | **Phase H** |

---

## Seven questions — **pinned** (countersigned 2026-08-23)

### Q1 — VC-first; RCP → H; canonical credential record; bulk = named exclusion

### Q2 — G ships **rung 2 only**; rung 1 = F input; rung 3 = H

### Q3 — All Hold: `regulator_export`, date-range, demographics, snapshot + bulk issuance named exclusion

### Q4 — Offline signature verify + signed status list (Status List 2021 or equiv.); independence = no live Naadi call at check time

### Q5 — Binding + locator (sharpened)

| Element | Pin |
|---------|-----|
| Digest | Over F projection allowlist (I-F-5), not raw envelope |
| Audit fingerprint | Issuance-time `ledger_read_audit.result_fingerprint` / `query_id` |
| Stamps | `issued_at`, `issuer_key_id`, `blueprint_version`, `grading_outcome_version` |
| Raw `session_id` | **Not** in portable credential body |
| **`evidence_ref`** | **Opaque, non-guessable, per-credential-version, independently revocable** — see I-G-8 |
| Locator paths | **(a)** preferred: `evidence_ref` → issuer fetch → internal audited `get_session_evidence`; **(b)** holder OOB evidence bundle (no fetch) |
| `/v1/ledger/` | **No new query kind** (I-F-2 holds). No `get_evidence_by_digest` |
| Digest alg | From strategy-brief track when landed |
| Surface | Separate issuer prefix; writes `credential_ledger` only |

**Non-guessable:** forbid constructions a verifier can reconstruct from known inputs (e.g. `hash(session_id \|\| credential_id)`). Prefer CSPRNG opaque tokens stored issuer-side.

**Per-credential-version:** reissue rotates `evidence_ref`; old refs stop resolving even if session unchanged.

**Independently revocable:** issuer may invalidate `evidence_ref` without revoking the credential (compliance takedown / holder unlinking). **Risk flag:** may need a distinct status surface from credential revocation — resolve in G.a detail / before G.b (see below).

### Q6 — Four-way isolation; fail-closed issuance on F-read failure; no unsigned egress

### Q7 — API v1 additive; `blueprint_version` in body; digest frozen per credential version; no in-place alg migration

---

## Pinned invariants (I-G-1…8)

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **I-G-1** | Issuance reads **F projection path only** via audited `get_session_evidence` — never live authoring, never grading tables, never a second projection | Import greps + service wiring tests |
| **I-G-2** | Credential **portable body** is a closed field set; additions need design review | Schema allowlist test |
| **I-G-3** | Evidence digest over **F-projection allowlisted fields** only | Digest fixture / name-strip stability test |
| **I-G-4** | Four-way isolation: grading ↔ authoring ↔ `ncvet_audit` ↔ `ncvet_issuer` — no forbidden imports | **`test_g_a_four_way_isolation_by_import_grep`** |
| **I-G-5** | Revocation / status list append-only + signed; verifier trust = (public key, status list) | **G.b** |
| **I-G-6** | Fail-closed on F-read / audit-sink failure at issuance — no `credential_ledger` row without successful audited evidence read | G.a 503 + ledger absence test |
| **I-G-7** | Digest algorithm frozen once per credential version; alg change = new version | Schema / version tests; brief alg id when landed |
| **I-G-8** | **`evidence_ref`:** opaque, non-guessable, per-credential-version, independently revocable; **no new `/v1/ledger/` query kind**; issuer fetch resolves through issuer audited surface → existing F kinds only | G.a unit + route tests; closed `KNOWN_QUERY_KINDS` grep |

**Boundary sentence (doc pin):** *G does not re-project, re-audit as SoT, or re-derive rung-1 artifacts.*

### Named exclusions (full list)

| Exclusion | Origin |
|-----------|--------|
| `regulator_export` / date-range / demographics / snapshot | F carryover — Hold |
| Bulk credential issuance | G Q1 |
| RCP / rung 3 | → Phase H |
| Fully-online-only verify | Q4 reject |
| Raw `session_id` in portable credential | Q5 |
| **Public `evidence_ref` status endpoint** | **G-phase** — issuer-fetch-only revoke default; public liveness = recon oracle |

---

## Slice plan (signed off)

| Slice | Deliverable | Status |
|-------|-------------|--------|
| **G.a** | Issue E2E — scopes, `credential_ledger`, `ncvet_issuer`, projection-binding, fail-closed, isolation greps | **Closed** — 12/12 |
| **G.b** | Verify offline + status list (I-G-5); `ncvet_verifier`; status cites `credential_id` | **Closed** — 22/22 + assist/offline parity (23) |
| **G.c** | Observability + runbook; pin `audit_sink_failure_total` **`surface="credentials"`** label (or distinct family) | Optional after G.b |

RCP → **H**. Bulk → named exclusion. **Public `evidence_ref` status** → named exclusion (G-phase).

### G.a closed notes (carry-forwards)

| Topic | Pin |
|-------|-----|
| **Audit chain shape** | **One** `ledger_read_audit` row per issuance F-read (`caller_kind=ncvet_issuer`, `query_kind=get_session_evidence`) + **one** `credential_ledger` append. **`audit_query_id` is the trust-ladder linkage** (rung 2 → rung 1): forensic “what audit read produced this credential” = single join. Not two `ledger_read_audit` rows (would need a second ledger `query_kind`, forbidden by Q5). Negative-space test: `caller_kind` set ⊆ `{ncvet_issuer}` and `ncvet_audit ∉` set on issuer path |
| **Fail-closed metrics** | Shared `audit_sink_failure_total` — G.c pins **`surface="credentials"`** |
| **Ref revoke HTTP** | Always **404**; `error_kind=ref_revoked` vs `not_found` |
| **`evidence_ref` entropy** | `EVIDENCE_REF_ENTROPY_BYTES = 32` (256-bit CSPRNG) |

---

## G.a detail — **closed** (countersigned 2026-08-23)

**Goal:** One end-to-end credential issue for one graded session — F.a-shaped gate.

### Route prefix

| Surface | Prefix | Notes |
|---------|--------|-------|
| F NCVET **reads** | `/v1/ledger/` | Unchanged; closed kinds |
| G **issuer** | **`/v1/credentials/`** | Separate audited prefix — same discipline as keeping F off `/v1/authoring/` |

| Method | Path | Scope | Behavior |
|--------|------|-------|----------|
| `POST` | `/v1/credentials/issue` | `ncvet:issue_credential` | Body: `{ "session_id": "..." }` (issuer-internal input only). Performs audited F read as `ncvet_issuer` → sign → append `credential_ledger` → return VC-shaped credential (**no raw `session_id` in response body**; includes `evidence_ref`) |
| `GET` | `/v1/credentials/evidence/{evidence_ref}` | **`ncvet:fetch_evidence_by_ref`** (split — issue≠fetch) | Resolves ref → internal `get_session_evidence`; returns F projection JSON; writes `ledger_read_audit` |

**Fetch-scope name pinned:** **`ncvet:fetch_evidence_by_ref`** — the `by_ref` suffix names the lookup discipline (opaque ref, not `session_id` / F identifiers). Blocks future `ncvet:fetch_evidence_by_session_id`-shaped drift.

**`caller_kind` forward-reference:** G.a lands **`ncvet_issuer`** for issuance-time and issuer-path evidence reads. G.b adds **`ncvet_verifier`** as a separate closed-enum entry (verify/status-list audit) — named here so the enum shape is “issuer now, verifier next,” not an accidental omission.

**Non-routes in G.a:** verify, revoke, status list (G.b).

### Scopes

| Scope | G.a? | Implies |
|-------|------|---------|
| `ncvet:issue_credential` | **Yes** | Issue only; does **not** imply F `ncvet:read_*`, does **not** imply verify |
| `ncvet:fetch_evidence_by_ref` | **Yes (preferred)** | Resolve `evidence_ref` only |
| `ncvet:verify_credential` / status | **G.b** | — |
| F `ncvet:read_session_evidence` | Unchanged | Issuer **service** calls F internally with issuer service identity — **not** by elevating the caller’s F read scope |

### `caller_kind` closed-enum extension

| Kind | Phase | Use |
|------|-------|-----|
| `ncvet_audit` | F | Regulator / tool F reads |
| **`ncvet_issuer`** | **G.a** | Issuance-time and `evidence_ref` fetch audited reads of `get_session_evidence` |
| **`ncvet_verifier`** | **G.b** (forward-ref) | Verify / status-list audit — **not** landed in G.a |

Extend `KNOWN_AUDIT_CALLER_KINDS` in `caller_kinds.py` + migration CHECK to include **`ncvet_issuer`** (same discipline as 012). Every credential issuance ⇒ **≥1** `ledger_read_audit` row for the backing evidence read (`caller_kind=ncvet_issuer`) — audit chain: *which evidence backed which credential*.

### `credential_ledger` schema outline (min columns)

Append-only. Migration **014** (after F’s 012/013). Checklist required before app code.

| Column | Type / notes |
|--------|----------------|
| `credential_id` | Stable id (UUID) |
| `credential_version` | Int; reissue increments |
| `tenant_id` | Tenant bound |
| `evidence_ref` | Opaque token; unique; **not** derivable from `session_id` |
| `session_id` | **Issuer-internal only** — never in portable VC body / never in status-list public entries |
| `issued_at` | timestamptz |
| `revoked_at` | nullable — credential revocation (G.b) |
| `evidence_ref_revoked_at` | nullable — **independent** ref invalidation (I-G-8). **CHECK:** `evidence_ref_revoked_at IS NULL OR evidence_ref_revoked_at >= issued_at` (DB-enforced) |
| `projection_digest` | Hex digest over allowlisted projection fields |
| `digest_alg` | Alg id (placeholder until brief pin; then frozen per version) |
| `audit_query_id` | FK-ish to issuance-time `ledger_read_audit.query_id` |
| `audit_fingerprint` | `result_fingerprint` at issue |
| `blueprint_version` | From F envelope |
| `grading_outcome_version` | Grading pipeline version stamp |
| `issuer_key_id` | Signing key id |
| `credential_bytes` / canonical JSON | Stored signed artifact (or hash + object store — pin storage in impl: **prefer row bytes for MVP**) |

Indexes: `(tenant_id, credential_id, credential_version)` unique; unique `evidence_ref` where `evidence_ref_revoked_at IS NULL` (or always unique historically + resolve checks revocation).

### Portable credential body (closed set — I-G-2)

Include: credential id/version, `evidence_ref`, `projection_digest`, `digest_alg`, `audit_fingerprint` (or `audit_query_id`), `issued_at`, `issuer_key_id`, `blueprint_version`, `grading_outcome_version`, proof/signature block.  
**Exclude:** raw `session_id`, demographics, learner free-text, F full projection payload.

### Files (expected)

| File | Change |
|------|--------|
| `audit/caller_kinds.py` | Add `ncvet_issuer` |
| Migration `014_credential_ledger.sql` + checklist | Table + CHECKs |
| `shared/schemas/ledger_read.py` (or credentials schema) | `ConsentScope` issue + fetch |
| `credentials/*.py` (new package) | Issue service, evidence_ref store, sign, routes under `/v1/credentials/` |
| F ncvet read service | Callable from issuer with `caller_kind=ncvet_issuer` (no new query kind) |
| `tests/test_authoring_g_a.py` | Matrix below |

### Test matrix (`test_authoring_g_a.py`) — acceptance spec

| # | Test | Protects |
|---|------|----------|
| **1** | **Happy issue** — scoped issuer → 200 → portable body has closed fields; **no** `session_id` key; `evidence_ref` present | I-G-2, I-G-8 |
| **2** | **Projection-binding** — `credential_ledger.projection_digest` equals digest of projection returned by issuance-time F read | I-G-3 |
| **3** | **Audit chain** — one `ledger_read_audit` row with `caller_kind=ncvet_issuer`, matching fingerprint on ledger | I-G-1, boundary sentence |
| **4** | **Scope deny** — no `ncvet:issue_credential` → 403; no ledger row | Scope gate |
| **5** | **Fail-closed** — F read / audit sink fails → 503; **no** `credential_ledger` row | I-G-6 |
| **6** | **`test_g_a_four_way_isolation_by_import_grep`** — issuer package does not import grading or authoring live-draft; only F audited boundary | I-G-4 |
| **7** | **Closed caller_kind** — unknown kind rejected; `ncvet_issuer` accepted | Enum extension |
| **8** | **No new ledger query kinds** — `KNOWN_QUERY_KINDS` / F live set unchanged (grep or equality fixture vs F freeze list) | I-G-8 / I-F-2 |
| **9** | **`evidence_ref` opacity** — ref not equal to `session_id`, not equal to `sha256(session_id\|\|…)` of known construction under test | I-G-8 |
| **10** | **Evidence fetch by ref** — valid ref + fetch scope → projection; revoked ref → 404/410; does not require F read scope on caller | I-G-8 |
| **11** | **Grading isolation** — finalize/append path does not import credentials package | I-G-4 / F.a parity |
| **12** | **Reissue rotates ref** — new `credential_version` ⇒ new `evidence_ref`; old ref does not resolve | I-G-8 |

### G.a non-goals

- Status list / verify / credential revoke API (G.b)
- RCP (H)
- Bulk issue
- Digest alg finalization (brief track; placeholder ok in tests)
- Adding kinds under `/v1/ledger/`

### Risk flag (estimate slip — catch here, not in G.b diff)

**`evidence_ref` revocation vs credential revocation:** I-G-8 requires independent ref invalidation. G.b status list today is framed around **`credential_id`**. If ref-level takedown needs a **public** verifier-visible signal (not only issuer fetch 404), that may require:

- a **second** status-list bit/stream for refs, or  
- encoding ref status inside the same list with a distinct entry type, or  
- keeping ref revocation **issuer-fetch-only** (verifier offline-checks credential signature + credential status; evidence freshness is online-fetch or OOB only).

**G.a detail pin (locked):** ref revocation is **issuer-fetch-only** (`evidence_ref_revoked_at`); credential revocation is **G.b status list**. Public ref status = **named exclusion** (see table above).

---

## G.b — Verify + status list (**detail in review**)

**Goal:** Offline credential verification + signed status-list distribution (I-G-5, Q4). Does **not** re-open G.a issuance. Public `evidence_ref` status remains a **named exclusion**.

**Scope countersigned 2026-08-23** with six detail pins below.

---

### Countersign pins (folded from scope walk)

| # | Pin |
|---|-----|
| **1** | Signing envelope carries **`signed_at` + `valid_until` inside the signed payload** — freshness/expiry anchors independent of transport-layer timestamps |
| **2** | **Key-retirement policy named now** (mechanism may be Phase I): keys retire via explicit deprecation window, minimum **N months** (**N = TBD placeholder** — pin numeric value before prod keyring ops) |
| **3** | List-fetch / staleness default = **Option A: hard-fail beyond max staleness**; generous default **`max_staleness = 7 days`**; **Option B** (soft-fail + warning) available as verifier-configurable override; **Option C** (assurance-tiered) = named future concern, out of G.b |
| **4** | Scope-split matrix: **`test_g_b_scope_split_matrix_issue_verify_fetch_are_disjoint`** — six-cell (issue / verify / fetch_status × grant / deny) |
| **5** | Migration **015** checklist: **apply-in-prod-before-deploy** (same discipline as 012) |
| **6** | **`caller_kind=ncvet_verifier`** — `KNOWN_AUDIT_CALLER_KINDS` + DB `ck_audit_caller_kind` + `validate_audit_event`; same commit as verify/status code |

---

### Routes / surfaces

| Method | Path | Scope | Behavior |
|--------|------|-------|----------|
| `POST` | `/v1/credentials/revoke` | `ncvet:revoke_credential` (or issue-adjacent revoke scope — **prefer distinct `ncvet:revoke_credential`**) | Sets `credential_ledger.revoked_at`; appends status-list entry; **does not** set `evidence_ref_revoked_at` |
| `GET` | `/v1/credentials/status_list` | **`ncvet:fetch_status_list`** | Cursor-paginated signed snapshots / deltas; per-page audit with `caller_kind=ncvet_verifier` |
| `GET` | `/v1/credentials/jwks` (or well-known) | Public / weakly auth — **pin: public JWKS read is OK**; not a credential operation | Issuer public keys for offline verify |
| — | Offline verify library / CLI | No Naadi call at verify time | Input: credential bytes + status-list snapshot + keyring |

**Non-routes:** public `evidence_ref` status; any new `/v1/ledger/` kind.

**Offline verify state machine** (pins 1–3 composed — for verifier implementers / Phase H RCP):

```text
(a) Parse status-list envelope
    → reject if valid_until <= signed_at   [pre-signature; nonsense envelope]
(b) Resolve key_id from trust root / keyring
    → reject if key unknown
    → reject if key retired beyond deprecation window
       (Option B: per-invocation flag only — AUDIT-level log with reason)
(c) Verify status-list signature
    → reject on bad sig
(d) Check valid_until > now
    → hard reject always
(e) Check age(signed_at) vs max_staleness (default 7d)
    → Option A hard-fail (default)
    → Option B: per-call flag + AUDIT log (not config-file sticky)
(f) Check credential_id against revoked entries
    → reject if revoked
(g) Verify credential signature (same keyring discipline)
    → accept
```

Five reject-points (a–f, plus credential sig), two Option-B override paths (b, e), one success path.

**Credential envelope parse (pin 1 companion):** verifiers MUST reject credentials where claim-level `valid_until <= signed_at` **at parse time, before signature verification** — same nonsense-envelope discipline. (G.a credentials may omit these claims until G.b issue stamps them; offline verify applies the check when present.)

**Key retirement decision criteria (pin 2):** window must exceed **maximum expected trust-root staleness for offline verifiers by at least 2×**. Numeric **N months = TBD** before prod keyring ops (expect ~3 if monthly sync, ~6 if sync cadence unknown).

**Option B (pin 3):** MUST be a **per-invocation flag**, not a sticky config-file setting. Override emits **AUDIT**-level log with reason — not warn, not silent.

---

### Scopes (closed; zero implication)

| Scope | Purpose | Implies |
|-------|---------|---------|
| `ncvet:issue_credential` | G.a — unchanged | **Nothing** in G.b |
| `ncvet:fetch_evidence_by_ref` | G.a — unchanged | **Nothing** in G.b |
| **`ncvet:verify_credential`** | HTTP verify assist (optional) / audit tagging | Does **not** imply issue / fetch_ref / fetch_status / revoke |
| **`ncvet:fetch_status_list`** | Pull signed status-list pages | Does **not** imply verify / issue / fetch_ref / revoke |
| **`ncvet:revoke_credential`** | Mark credential revoked + publish list update | Does **not** imply issue; does **not** revoke `evidence_ref` |

Five scopes across G.a+G.b — **no overlaps, no supersets**. Verifier scopes must not grant issuer capabilities and vice versa.

---

### `caller_kind` + migration 015

| Kind | Phase | Use |
|------|-------|-----|
| `ncvet_issuer` | G.a | Issue + evidence_ref fetch F-reads |
| **`ncvet_verifier`** | **G.b** | Status-list fetch audit (and any online verify assist) |

**Deploy order (checklist `015_credential_status_list_checklist.md`):**

1. Apply migration 015 (CHECK adds `ncvet_verifier`; status-list storage tables).
2. Verify `ck_audit_caller_kind` includes `ncvet_verifier`.
3. Deploy app with verify/status/revoke routes.

Same “apply-in-prod-before-deploy” note as 012 — if app deploys first, verify audit inserts fail at CHECK.

---

### Status-list envelope (signed payload — pin 1)

```text
status_list_schema_version: "status_list.v1"
digest_alg / signature_alg: frozen for this schema version
key_id: issuer_key_id used for this snapshot
signed_at: timestamptz          # inside signature
valid_until: timestamptz        # inside signature; hard expiry
entries: [ { credential_id, revoked_at? }, ... ]   # credential_id ONLY
cursor / page metadata: as needed for pagination
proof: signature over canonical bytes of the above
```

| Rule | Pin |
|------|-----|
| Minimization | Entries cite **`credential_id` only** — no `evidence_ref`, session digest, audit fingerprint, `session_id` |
| Version | Unknown `status_list_schema_version` → reject closed (no best-guess alg) |
| Alg freeze | Change alg = **new schema version**; v1 support continues |
| Key rotation | New lists may use new `key_id`; **no in-place re-sign** of historical list bytes under a new alg within the same schema version |
| Key retirement | Explicit deprecation window per decision criteria above; retirement ≠ rotation |
| Freshness | Verifier uses **`signed_at`** vs local clock for max-staleness; **`valid_until`** is hard reject regardless of Option B |

### List-fetch / staleness policy (pin 3)

| Regime | Behavior |
|--------|----------|
| `now ≤ valid_until` AND age(`signed_at`) ≤ `max_staleness` (default **7d**) | Accept list |
| age(`signed_at`) > `max_staleness` but `now ≤ valid_until` | **Default Option A:** hard-fail. **Option B:** per-call flag + AUDIT log |
| `now > valid_until` | Hard reject always |
| Option C (assurance-tiered) | **Out of G.b** |

---

### Storage sketch (migration 015)

| Table / object | Role |
|----------------|------|
| `credential_status_list_snapshot` | Append-only signed envelope bytes + `key_id` + `signed_at` + `valid_until` + schema version |
| `credential_ledger.revoked_at` | Already in 014 — G.b revoke writes here |
| JWKS / keyring | `issuer_key_id` → public material; retirement metadata |

---

### I-G-5 enforcement (G.b)

| Sub-pin | Test / structural |
|---------|-------------------|
| Status list append-only + signed | Cannot UPDATE historical snapshot bytes in place |
| Verifier trust = (public key, status list) | Offline verify suite with no network to issuer HTTP |
| Four-way isolation | Verify path does not import issue write path; grading unaffected (I-G-4 extended) |

---

### Test matrix (`test_authoring_g_b.py`) — acceptance spec (expanded)

| # | Test | Protects |
|---|------|----------|
| **1** | Offline verify happy — valid sig + fresh list + not revoked → accept | Q4, I-G-5 |
| **2** | **`test_g_b_revoking_credential_does_invalidate_future_verifications`** | I-G-5 / Delta 2 |
| **3** | Tampered credential → signature fail | I-G-5 |
| **4** | Tampered / wrong-key list → reject | Pin 1 |
| **5** | List past `valid_until` → reject even under Option B | Pin 1 |
| **6** | **`test_g_b_valid_until_le_signed_at_rejects_pre_signature_check`** | Pin 1 |
| **7** | Max-staleness Option A — age > 7d → hard-fail | Pin 3 |
| **8** | **`test_g_b_staleness_option_b_override_requires_per_call_flag_and_emits_audit`** | Pin 3 |
| **9** | `ncvet_verifier` accepted; unknown rejected at app enum | Pin 6 |
| **10** | **`test_g_b_ncvet_verifier_caller_kind_unknown_at_app_enum_raises`** | Pin 6 |
| **11** | **015 SQL belt** — `ncvet_verifier` in CHECK (grep migration file) | Pin 5 / 6 |
| **12** | Status-list HTTP fetch emits `caller_kind=ncvet_verifier` | Pin 6 |
| **13** | **`test_g_b_scope_split_matrix_issue_verify_fetch_are_disjoint`** — route-level six-cell | Pin 4 / Q4 |
| **14** | **`test_g_b_verifier_scopes_do_not_grant_issuer_capabilities`** — G.a/G.b boundary | Q4 |
| **15** | Status-list pagination cursor + cap | F.b discipline |
| **16** | Entry minimization — recursive grep no `session_id` / `evidence_ref` | Q5 |
| **17** | **`test_g_b_revoking_evidence_ref_does_not_invalidate_prior_verifications`** | I-G-8 / Delta 2 |
| **18** | No new `/v1/ledger/` kinds | Q5 |
| **19** | Key rotation without re-sign — old list verifies under old `key_id` | Pin 2 |
| **20** | **`test_g_b_retired_key_within_deprecation_window_verifies_with_warning`** | Pin 2 |
| **21** | **`test_g_b_retired_key_beyond_deprecation_window_hard_fails`** | Pin 2 |
| **22** | **`test_g_b_audit_chain_join_reconstructs_credential_to_issue_audit`** — join `credential_ledger.audit_query_id` → exactly one `ledger_read_audit` with **`caller_kind=ncvet_issuer`** (rung 2→1); plus status fetch emits separate `ncvet_verifier` row | Delta 1 |

**Delta 1 note:** The join proves **issuance** provenance (`ncvet_issuer`), not verify. Verify emits its own `ncvet_verifier` audit rows; conflating them would break Q5’s single-F-read chain.

### G.b non-goals

- Public `evidence_ref` liveness endpoint
- Option C assurance-tiered staleness
- Sticky config-file Option B
- RCP / re-grade (H)
- Changing G.a issue path or F live kinds
- Bulk revoke-as-export
- Numeric key-retirement **N** (placeholder until ops pin)

### Gate

**Countersigned 2026-08-23** — G.b closed. Migration 015 + enum + routes + 22-case matrix green; G.a regression 12/12 held.

### G.b closed notes (carry-forwards → Phase H prelude)

| # | Topic | Pin |
|---|-------|-----|
| **1** | **Assist ≡ offline** | `test_g_b_verify_assist_endpoint_agrees_with_offline_verdict` — `/verify` must not grow independent accept/reject semantics |
| **2** | **Numeric key-retirement N** | Criteria locked: **N ≥ 2× max trust-root staleness**; value TBD; code reads `CREDENTIAL_KEY_DEPRECATION_DAYS` with loud TBD comment — do not hardcode a production guess |
| **3** | **Verifier metrics single call site** | When verifier Prometheus wiring lands: `record_ncvet_verifier_audit_metrics` (or credentials-surface equivalent) — I-E3-10 grep belt, same shape as catalog / NCVET-read |

### G.b acceptance inventory

Pins 1–3 state machine; route-level six-cell scope split; cross-boundary issuer/verifier; positive `audit_query_id` join; revoke-symmetry pair (evidence_ref vs credential); 015 belt; Option B per-call + AUDIT; non-goals held (no new `/v1/ledger/` kinds, no public `evidence_ref` status, Option C out).

---

## Strategy-brief handoff

Digest alg → I-G-3 / I-G-7 when landed. One digest story.

---

## Next step

**Phase H (RCP) closed** — see [`authoring_harness_phase_h.md`](./authoring_harness_phase_h.md).  
**Phase I (ARP) prelude** — see [`authoring_harness_phase_i.md`](./authoring_harness_phase_i.md). G.c optional follow-on.
