# Clinician review UI — minimum slice (Tier 1 / Tier 2 signature primitive)

**Parent:** [`authoring_harness.md`](./authoring_harness.md) · trust ladder · post-ladder  
**Predecessor:** SAMVAAD.e **closed** (79/79) · Ramesh fragment **countersigned** (`80e29c6e…`)  
**Status:** **Countersigned (2026-08-30)** — code may open against matrix below  
**Framework stop rule:** inherited unchanged — no review-ui exceptions.

## Boundary sentence

The clinician review UI persists **one append-only preceptor sign-off event** over a
**replayable ledger digest**, producing Tier 1 (completion receipt) and Tier 2
(preceptor attestation) **certificates as deterministic projections** — never
hand-authored documents and **never** a “certified competent” claim (Tier 3).

NAADI is the evidence engine; the **institution** (college / Virohan programme)
is the attesting party on the certificate face.

---

## Convergence pin (why this slice exists now)

Everything on the board converges on **Ramesh signed**. Sequencing:

| # | Track | Status | Notes |
|---|-------|--------|-------|
| 1 | **I.c.1 cutover** | **Committed · clock-gated** | Ops annotation + smoke **2026-09-22** — no new engineering decisions |
| 2 | **This slice** | Scope doc → countersign → implement | One table · one screen · one sign-off event · **migration 024 rides with review-UI PR** |
| 3 | **Cardiologist calendar** | **Critical path — hard deadline** | Date → UI cut → countersign sitting → first signed case ([Ramesh README](../../content/ramesh-stemi-draft/README.md)) — **milestone one** of the ₹2.5L pilot ([Wedge A gate](../corpus/wedge_a_emergency_block.md)) |
| 4 | **Ramesh disposition** | Two pins below | Resolve before the room, not in it |
| — | Certificate rendering · new cases · Virohan one-pagers | **Frozen** | Wait behind first signature |

**Merge order (unchanged):** authoring-repair precursor → `.d` → `.e` → cutover →
CF-2 reverify → I-S-13 evidence accumulation.

**Drift enforcement (Wedge C):** `test_eval_import_boundary.py` — `eval/` must not
import `persona/`, `tenant/`, or frontend paths; rides this PR, fails CI not review.

**Paper fallback:** If UI is not ready by the meeting, cardiologist reviews the
narrative debrief doc and signs on paper referencing the **ledger digest**;
operator **backfills** the signature row with
`signature_method='paper_backfill'`. Still a valid Tier 2 artifact.

---

## Claim ladder (document language — frozen)

| Tier | Name | On the document | Claim owner |
|------|------|-----------------|-------------|
| **1** | Completion receipt | “Learner X **completed** simulation case Y on date Z, **N/M rubric hits attached**; evidence ledger linked.” | Institution (receipt) |
| **2** | Preceptor attestation | Tier 1 + named preceptor: “I reviewed this debrief and attest the performance meets our programme’s standard.” | Named preceptor |
| **3** | “Certified competent” | **Does not exist** | Regulator / hospital credentialing only |

**Forbidden words on any generated artifact (Tier 1 and Tier 2):**

- `competent`, `certified competent`, `licensed`, `credential issued by NAADI`
- **`passed`, `pass`, `failed`, `fail`** on **Tier 1** face language — Tier 1 makes
  **no threshold claim**. Rubric hits are **evidence attached**, not a grade
  conferred. (`grade_passed` may persist internally for replay; the certificate
  projector **must not** emit pass/fail prose on Tier 1.)

---

## Hash construction rules (named — verifier-readable)

A verifier six months out must not read source to pick a fourth rule. **Pick from
this table only.** If canonicalization ever changes, bump the digest kind suffix
(`ledger_digest_v2`) — never silently redefine `_v1`.

| Rule | Hash name | Input payload | Serialization | Digest prefix |
|------|-----------|---------------|---------------|---------------|
| **A** | `case_version_hash` | `CaseBlueprintV2` hashed sections (see below) | `json.dumps(…, sort_keys=True, separators=(",", ":"), default=str)` | **none** — 64-char lowercase hex |
| **B** | `rubric_version_hash` | `GradingBlueprint.canonical_dict()` | same as A | **none** — 64-char lowercase hex |
| **C** | `ledger_digest_v1` | Ordered session evidence events (see below) | same as A | **`sha256:`** + 64-char hex |

References: rule A → [`case_v2.py`](../../../shared/schemas/case_v2.py) ·
[`authoring_harness.md`](./authoring_harness.md) §2.2 · rule C → § ledger_digest_v1 below.

### A. `case_version_hash` (blueprint substance)

**Source:** `CaseBlueprintV2.compute_content_hash()` — do not fork.

| Field | Rule |
|-------|------|
| **Payload sections** | `identity`, `targeting`, `patient`, `clinical_truth`, `environment`, `physiology`, `interaction`, `grading_blueprint`, `schema_version` |
| **Excludes** | `provenance` and all workflow metadata |

### B. `rubric_version_hash` (sealed rubric at finalize)

**Source:** `GradingBlueprint.canonical_dict()` at grade time (Nirikshak
`evaluation_manifest_hash` substance).

### C. `ledger_digest_v1` (immutable session evidence root)

**Construction:**

1. Load all **session evidence events** for `(tenant_id, session_id)` from the
   authoritative event store (Postgres session-event projection at finalize; web
   ledger must converge before production sign-off).
2. **Order:** `captured_at_utc ASC`, `event_id ASC` (`.e` tiebreaker discipline).
3. **Canonical event record** (allowlisted, privacy-stripped): `event_id`,
   `captured_at_utc`, `event_kind`, `payload`.
4. **Normalize before `json.dumps`** (do not rely on `default=str` for these types):
   - `datetime` → UTC `.isoformat()` (e.g. `2026-08-30T12:34:56.789000+00:00`)
   - `Decimal` → plain decimal string via `format(d, "f")` (e.g. `148.0`, `30.5`)
   - `UUID` → lowercase hex string
5. **Payload array:** ordered canonical records from steps 2–4.
6. **Serialization:** `json.dumps(array, sort_keys=True, separators=(",", ":"), default=str)`
7. **Digest:** `SHA-256` → wire form **`sha256:{hex}`**

**`default=str` contract (load-bearing):** any remaining non-JSON-native type
falls through `default=str`. That behavior is **frozen by test**, not assumed
from stdlib stability. Fixture event (row **7** / **7b**):

```json
[
  {
    "captured_at_utc": "2026-08-30T12:34:56.789000+00:00",
    "event_id": "ev_contract_001",
    "event_kind": "vitals_sample",
    "payload": {"sbp": "148.0", "note": "contract pin"}
  }
]
```

Pinned canonical string (exact bytes hashed):

```text
[{"captured_at_utc":"2026-08-30T12:34:56.789000+00:00","event_id":"ev_contract_001","event_kind":"vitals_sample","payload":{"note":"contract pin","sbp":"148.0"}}]
```

→ `ledger_digest_v1` = `sha256:` + SHA-256 of that UTF-8 string. If Python or
normalization changes, the test halts the walk — digest must not fork silently.

**Alignment belt:** at finalize, `session_ledger.replay_hash` **must equal**
`ledger_digest_v1`. Sign-off rejects on mismatch.
[`ledger/writer.py`](../../../services/pratibimb/ledger/writer.py) converges in
the review-UI PR.

---

## Scope walk — surfaces touched

| Surface | Prior state | Review-UI intent |
|---------|-------------|------------------|
| `web/` debrief route | Nirikshak grade + local ledger | Read-only replay: actions, vitals trace, rubric hits |
| `services/pratibimb/ledger/` | `session_ledger.replay_hash` | Align to `ledger_digest_v1`; authoritative at sign-off |
| **Migration 024** | — | `clinical_review_signature` INSERT-only + grant discipline |
| **New route** | — | `POST /v1/review/sign-off` · `POST /v1/review/revoke` (dev auth → RBAC) |
| Certificate manifest / PDF | — | Projector slice in same PR; QR keyed on **manifest hash** |

### Orthogonality (explicit)

- Does **not** touch SAMVAAD 021/022 evidence tables or `.e` projectors.
- Does **not** amend ARP / regrade / credential issuance (Phase I/H surfaces).
- Does **not** promote `corpus_tier` or retroactively upgrade practice runs.
- Does **not** add Tier 3 language or Dhaara summative lift.
- Does **not** build the full review product (comment threads, multi-reviewer workflow, case authoring).

### Invariants at stake

- **INSERT-only (mechanism, not convention):** app DB role has **INSERT + SELECT
  only** on `clinical_review_signature`; **no UPDATE / DELETE** grants. Corrections
  and withdrawals are **new rows** (revocation row with `revokes_signature_id`).
- **Digest binding:** sign-off stores server-computed `ledger_digest_v1`; mismatch → fail-closed.
- **Version binding:** `case_version_hash` + `rubric_version_hash` pinned at sign time.
- **Statement-type storage boundary:** Tier 1 / Tier 2 `attestation_text` rules enforced by **CHECK**, not projector policy.
- **`corpus_tier` on the face:** practice/draft/signed visible; forward-only upgrade after Ramesh sign.
- **Institution attests:** `institution_id` required; NAADI notarizes only.

---

## Sign-off event shape (API + persistence)

### HTTP request (`POST /v1/review/sign-off`)

```json
{
  "session_id": "sess_…",
  "statement_type": "tier_2_attestation",
  "attestation_text": "I reviewed this debrief and attest the performance meets our programme's standard.",
  "institution_id": "virohan-programme-…",
  "institution_display_name": "Virohan — GNM Cohort 2026",
  "signature_method": "digital",
  "paper_reference": null
}
```

Server resolves — **reject if client-supplied**:

- `ledger_digest` ← `ledger_digest_v1(session events)`; must match `session_ledger.replay_hash`
- `case_version_hash` ← `session_ledger.blueprint_content_hash` (rule A)
- `rubric_version_hash` ← rule B at finalize
- `corpus_tier`, `assessment_mode`, `rubric_hits_json`, `preceptor_*`

### Revocation request (`POST /v1/review/revoke`)

```json
{
  "revokes_signature_id": "sig_…",
  "reason": "Signed in error — session replay reviewed offline."
}
```

Inserts a **new row** with `signature_kind='revocation'`, `revokes_signature_id`
set, same `ledger_digest` binding. Verify response for the revoked artifact:
`status=revoked`, `superseded_by=<new signature_id>`. Original row unchanged.

**Revocation chain (pinned — pick one, not ambiguous):**

| Rule | Detail |
|------|--------|
| **Target** | `revokes_signature_id` must reference a row with `signature_kind='sign_off'` only |
| **Terminal** | Revocation rows **cannot be revoked** — `POST /v1/review/revoke` on a revocation id → `illegal_transition` |
| **Re-attestation** | After withdrawal, preceptor issues a **new** `sign_off` / `tier_2_attestation` row — not a second revocation |
| **Audit** | Revocation row carries its own `preceptor_subject_id`, `signed_at_utc`, `attestation_text` (reason) |

API enforces target-kind check; matrix row **9** asserts revoke-of-revocation rejection.

### Sign-off response

```json
{
  "signature_id": "sig_…",
  "ledger_digest": "sha256:abc…",
  "case_version_hash": "80e29c6e…",
  "rubric_version_hash": "…",
  "corpus_tier": "draft",
  "statement_type": "tier_2_attestation",
  "signed_at_utc": "2026-…"
}
```

---

## Migration 024 — `clinical_review_signature`

**Ships in the review-UI PR** — no separate migration stream.

```sql
-- 024_clinical_review_signature.sql
-- Clinician review UI: append-only preceptor sign-off (Tier 1 / Tier 2 primitive)
-- NAADI notarizes; institution attests. App role: INSERT + SELECT only.

CREATE TABLE clinical_review_signature (
    signature_id                TEXT        PRIMARY KEY,
    tenant_id                   TEXT        NOT NULL,
    session_id                  TEXT        NOT NULL,
    ledger_digest               TEXT        NOT NULL,
    rubric_version_hash         TEXT        NOT NULL,
    case_version_hash           TEXT        NOT NULL,
    corpus_tier                 TEXT        NOT NULL
        CHECK (corpus_tier IN ('draft', 'practice', 'signed')),
    assessment_mode             TEXT        NOT NULL,
    signature_kind              TEXT        NOT NULL DEFAULT 'sign_off'
        CHECK (signature_kind IN ('sign_off', 'revocation')),
    statement_type              TEXT        NOT NULL
        CHECK (statement_type IN ('tier_1_completion', 'tier_2_attestation')),
    attestation_text            TEXT,
    preceptor_subject_id        TEXT        NOT NULL,
    preceptor_display_name      TEXT        NOT NULL,
    institution_id              TEXT        NOT NULL,
    institution_display_name    TEXT        NOT NULL,
    signed_at_utc               TIMESTAMPTZ NOT NULL,
    signature_method            TEXT        NOT NULL
        CHECK (signature_method IN ('digital', 'paper_backfill')),
    paper_reference             TEXT,
    revokes_signature_id        TEXT
        REFERENCES clinical_review_signature (signature_id),
    rubric_hits_json            JSONB       NOT NULL,
    grade_total                 DOUBLE PRECISION NOT NULL,
    grade_passed                BOOLEAN     NOT NULL,
    hits_required               INTEGER     NOT NULL,
    hits_scored                 INTEGER     NOT NULL,
    CONSTRAINT ck_statement_type_attestation_text
        CHECK (
            (statement_type = 'tier_1_completion' AND attestation_text IS NULL)
            OR (statement_type = 'tier_2_attestation' AND attestation_text IS NOT NULL
                AND length(trim(attestation_text)) > 0)
        ),
    CONSTRAINT ck_revocation_shape
        CHECK (
            (signature_kind = 'sign_off' AND revokes_signature_id IS NULL)
            OR (signature_kind = 'revocation' AND revokes_signature_id IS NOT NULL)
        ),
    CONSTRAINT ck_digest_formats
        CHECK (
            ledger_digest LIKE 'sha256:%'
            AND length(case_version_hash) = 64
            AND length(rubric_version_hash) = 64
        )
);

CREATE UNIQUE INDEX ux_review_signature_session_type_preceptor
    ON clinical_review_signature (tenant_id, session_id, statement_type, preceptor_subject_id)
    WHERE signature_kind = 'sign_off';

CREATE INDEX ix_review_signature_ledger_digest
    ON clinical_review_signature (tenant_id, ledger_digest);

CREATE INDEX ix_review_signature_revokes
    ON clinical_review_signature (revokes_signature_id)
    WHERE revokes_signature_id IS NOT NULL;

-- Grant discipline (representative — exact role names from deploy config)
REVOKE UPDATE, DELETE ON clinical_review_signature FROM pratibimb_app;
GRANT INSERT, SELECT ON clinical_review_signature TO pratibimb_app;
```

**Tier 1:** `attestation_text` **must be NULL**. **Tier 2:** `attestation_text`
**NOT NULL** and `length(trim(attestation_text)) > 0` — empty and whitespace-only
strings rejected at CHECK (row **3**).

**Grants in 024:** `REVOKE UPDATE, DELETE` / `GRANT INSERT, SELECT` live in the
migration file — reproducible on every environment rebuild, not a separate ops step.

---

## Certificate projector (manifest-first determinism)

**Inputs (frozen tuple):**

`(ledger_digest, rubric_version_hash, case_version_hash, signature_events[])`

where `signature_events[]` is ordered `signed_at_utc ASC, signature_id ASC`.

**Primary artifact:** `certificate_render_manifest_v1` — canonical JSON (rule A/B/C
serialization), **byte-identical** across two projector runs.

**QR / verify URL:** keyed on `SHA-256(manifest_bytes)` — **never** PDF bytes.

**PDF:** presentation-only leaf rendered from manifest. If a PDF library embeds
generation timestamps, that does **not** affect verify — the matrix row asserts
manifest byte identity, not PDF byte identity (fallback pin from scope walk).

**Tier 1 face:** uses **completed** + hit counts; never **passed**.

**Forbidden-word discipline (testable):** row **8** sub-assertion **8b** greps
Tier 1 manifest `display_strings[]` against the closed list (`competent`, `passed`,
`pass`, `failed`, `fail`, …) — case-insensitive whole-word match. Face language
is a test, not a review-time convention.

---

## UI slice (minimum — one screen)

Route: `/review/$sessionId` (web) · gated to preceptor role.

| Panel | Content |
|-------|---------|
| **Header** | Case title · `corpus_tier` badge · `case_version_hash` (truncated) · `assessment_mode` |
| **Replay** | Chronological actions + vitals sparkline · turn-indexed chat (read-only) |
| **Rubric** | Required vs supporting hits · points · trap hits highlighted — **no pass/fail headline on Tier 1 receipt path** |
| **Digest** | `ledger_digest` copyable · link to verify/replay endpoint |
| **Sign-off** | Tier 2 attestation textarea · institution picker · **Sign** · **Revoke** (if prior signature exists) |

Build sequence (each step testable against prior artifact):

`review screen → ledger replay → sign-off persist (024) → manifest projector → verify URL`

---

## Ramesh disposition — pins before cardiologist

| Pin | **Disposition** |
|-----|-----------------|
| **Hypotension path-stacking** | Authoring-frozen for sign-off; engine enforcement post-sign |
| **V4R rubric candidate** | **Closed** — required trio in `0.2.0-draft-9hit` |

**Sign-off target:** hash `80e29c6e…` · rubric `0.2.0-draft-9hit`.

---

## Q-sharpening (resolved)

| # | Pin |
|---|-----|
| **Q1** | One table, one screen, one PR (024 + UI + routes + manifest projector). |
| **Q2** | Tier 1 / Tier 2 `attestation_text` enforced at CHECK boundary. |
| **Q3** | Verify replays `ledger_digest_v1` + manifest hash; revocation via new row. |
| **Q4** | Paper backfill first-class. |
| **Q5** | Append-only = grant discipline + revocation rows, not README convention. |
| **Q6** | Freeze target: **10 rows / 11 tests** (matrix below). |

---

## Acceptance matrix (freeze target)

**Disagreement line:** *10 semantic rows / 11 tests — one row split: row **3**
spans Tier 2 attestation rejection as **3a** (NULL) and **3b** (whitespace-only
`'   '`). Row **8** includes sub-assertion **8b** (forbidden-word grep) inside
the same test as **8a** (byte-identical manifest). All other rows are one test
each. One additional test comes from the row **3** split, not from row **8**.*

| # | Sub | Case | Asserts |
|---|-----|------|---------|
| **1** | | Sign-off happy path (Tier 2) | Fixed finalized session → INSERT with correct digests (rules A/B/C) |
| **2** | | Digest mismatch fail-closed | Recomputed `ledger_digest_v1` ≠ stored → reject; no row |
| **3** | **3a** | Tier 2 NULL attestation | `attestation_text IS NULL` → CHECK + API reject |
| | **3b** | Tier 2 whitespace-only | `attestation_text = '   '` → CHECK + API reject |
| **4** | | Tier 1 with attestation text | Non-NULL `attestation_text` on `tier_1_completion` → reject |
| **5** | | Duplicate sign-off | Same `(tenant, session, statement_type, preceptor)` → `illegal_transition` |
| **6** | | Paper backfill | `signature_method='paper_backfill'` persists |
| **7** | **7b** | `ledger_digest_v1` contract + determinism | Pinned canonical JSON bytes (§ C fixture) → expected `sha256:{hex}`; two full runs over event fixture → identical digest |
| **8** | **8a/8b** | Manifest projector | **8a:** two runs → byte-identical manifest JSON; **8b:** Tier 1 `display_strings` grep clean against forbidden list |
| **9** | | Revocation supersession | Revocation of `sign_off` → verify `revoked` + `superseded_by`; revoke-of-revocation → reject |
| **10** | | Grant discipline | App role cannot UPDATE/DELETE (migration + integration) |
| **11** | | Prior compose | SAMVAAD **79/79** unchanged |

**Marker:** `review_ui_acceptance`  
**Tests:** **11**

### Compose assertion (literal)

```text
samvaad_compose: 79
review_ui_acceptance: 11
→ post-review-ui harness compose: 90
```

---

## Review-UI PR manifest (closed scope — nothing else joins)

Single PR; build order: `screen → replay → persist (024) → manifest → verify`.

| Artifact | Detail |
|----------|--------|
| **UI** | One review screen: replay → rubric hits → sign-off |
| **Migration 024** | `clinical_review_signature` CREATE + grants + `statement_type` CHECK + append-only REVOKE |
| **Import boundary** | `test_eval_import_boundary.py` — `eval/` must not import `persona/`, `tenant/`, or frontend paths |
| **Scope doc** | This file (`review_ui.md`) — countersigned cross-refs only |

No other migrations, lobes, or features ride this PR.

---

## Gate

**Countersigned 2026-08-30.** Code opens against matrix above.

**Remaining human critical path:** cardiologist meeting date in
[`content/ramesh-stemi-draft/README.md`](../../content/ramesh-stemi-draft/README.md)
— hard deadline: **date → UI cut → countersign sitting → first signed case**.

---

## Deferred (explicit — post-first-signature)

- Institution letterhead PDF skin (manifest already deterministic)
- Tier 1 auto-emission on session finalize (web-local → Postgres sync)
- Engine-side hypotension stack enforcement (runtime relative-to-current)
- Public verify page UX polish beyond digest replay API
