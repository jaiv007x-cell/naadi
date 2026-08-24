# Ramesh Kale STEMI — Authoring Smoke Walkthrough

End-to-end exercise of the Phase A authoring workflow against the seeded Ramesh draft.
Uses dev-header auth (`AUTH_MODE=development`, `AUTH_ALLOW_DEV_HEADER=1`).

**Prerequisites**

- Pratibimb running on port 8100
- `authoring:submit` consent grant for the smoke author on tenant `ncvet-default`
- Draft id: `ramesh-kale-stemi-inferior-v1`

Set common headers:

```bash
export BASE=http://localhost:8100
export TENANT=ncvet-default
export AUTHOR=ramesh-author
export DRAFT=ramesh-kale-stemi-inferior-v1
export AUTH="-H X-Dev-Subject:$AUTHOR -H X-Dev-Tenant:$TENANT"
```

Issue the submit grant once (in-memory consent store in dev, or via your consent admin path):

```python
# Python one-liner in dev REPL / test setup
await consent.issue_grant(
    tenant_id="ncvet-default",
    subject_id="ramesh-author",
    scope=ConsentScope.AUTHORING_SUBMIT,
    resource_id="*",
    ...
)
```

---

## Step 1 — GET draft (baseline)

```bash
curl -s $AUTH "$BASE/v1/authoring/drafts/$DRAFT" | jq .
```

**Expected:** `200 OK`

| Field | Expected |
|-------|----------|
| `current_state` | `"DRAFT"` |
| `case_id` | `"ramesh_kale.stemi.inferior.v1"` |
| `content_hash` | 64-char hex |
| `grading_blueprint` | present after startup wire (may be absent on fresh DB before PUT) |

---

## Step 2 — PUT draft with smoke rubric

Attach the minimal three-hit rubric (`stemi.ecg_ordered`, `stemi.aspirin_chewed`, `stemi.pci_transfer`).

Take `blueprint_json` from step 1 and merge in `grading_blueprint` from
`services/pratibimb/authoring/ramesh_smoke.py`, or rely on startup wire and skip to step 3.

```bash
curl -s -X PUT $AUTH \
  -H "Content-Type: application/json" \
  "$BASE/v1/authoring/drafts/$DRAFT" \
  -d @docs/authoring/ramesh_smoke_put_body.json | jq .
```

**Expected:** `200 OK`

| Field | Expected |
|-------|----------|
| `current_state` | `"DRAFT"` |
| `content_hash` | changes from step 1 (rubric is hashed) |
| `blueprint_json.grading_blueprint.hits` | length 3 |

---

## Step 3 — PUT golden fixture pair

**Perfect path** (all required hits satisfied):

```bash
curl -s -X PUT $AUTH \
  -H "Content-Type: application/json" \
  "$BASE/v1/authoring/drafts/$DRAFT/fixtures" \
  -d '{
    "fixture_kind": "perfect_path",
    "trace_json": {
      "case_id": "ramesh_kale.stemi.inferior.v1",
      "case_version": "1.0.0",
      "duration_s": 0,
      "drugs": [{"drug_id": "aspirin", "dose": 325, "dose_unit": "mg", "route": "PO", "t_s": 180.0}],
      "flags": [],
      "orders": [{"order_id": "ecg_12l", "t_s": 120.0, "params": {}}],
      "dx": [],
      "recog": [],
      "escal": [{"target": "pci_transfer", "t_s": 400.0}],
      "handoffs": []
    },
    "expected_grade_json": {"passed": true}
  }' | jq .
```

**Expected:** `200 OK` — `{ "fixture_kind": "perfect_path", "content_hash": "<64 hex>" }`

**Critical miss** (aspirin only — ECG required hit fails):

```bash
curl -s -X PUT $AUTH \
  -H "Content-Type: application/json" \
  "$BASE/v1/authoring/drafts/$DRAFT/fixtures" \
  -d '{
    "fixture_kind": "critical_miss",
    "trace_json": {
      "case_id": "ramesh_kale.stemi.inferior.v1",
      "case_version": "1.0.0",
      "duration_s": 0,
      "drugs": [{"drug_id": "aspirin", "dose": 325, "dose_unit": "mg", "route": "PO", "t_s": 180.0}],
      "flags": [],
      "orders": [],
      "dx": [],
      "recog": [],
      "escal": [],
      "handoffs": []
    },
    "expected_grade_json": {"passed": false}
  }' | jq .
```

**Expected:** `200 OK` — `{ "fixture_kind": "critical_miss", "content_hash": "<64 hex>" }`

---

## Step 4 — POST dry-run (stable hash)

```bash
curl -s -X POST $AUTH "$BASE/v1/authoring/drafts/$DRAFT/dry-run" | jq .
```

**Expected:** `200 OK`

```json
{
  "passed": true,
  "result_hash": "<64 hex>",
  "outcomes": [
    { "fixture_kind": "critical_miss", "expected_passed": false, "actual_passed": false, "matched_expectation": true },
    { "fixture_kind": "perfect_path", "expected_passed": true, "actual_passed": true, "matched_expectation": true }
  ]
}
```

Run twice — `result_hash` must be identical (deterministic).

---

## Step 5 — POST submit (DRAFT → IN_REVIEW)

Requires `authoring:submit` grant.

```bash
curl -s -X POST $AUTH \
  -H "Content-Type: application/json" \
  "$BASE/v1/authoring/drafts/$DRAFT/submit" \
  -d '{"reason": "smoke walkthrough"}' | jq .
```

**Expected:** `200 OK`

| Field | Expected |
|-------|----------|
| `current_state` | `"IN_REVIEW"` |
| `transitions[-1].from_state` | `"DRAFT"` |
| `transitions[-1].to_state` | `"IN_REVIEW"` |
| `transitions[-1].dry_run_result_hash` | matches step 4 `result_hash` |

**Failure modes**

| Status | `detail.error` | Meaning |
|--------|----------------|---------|
| `403` | `SCOPE_DENIED` | missing `authoring:submit` grant |
| `409` | `MISSING_FIXTURES` | golden pair incomplete |
| `409` | `DRY_RUN_FAILED` | fixtures exist but Nirikshak disagreement; no transition row |
| `409` | `BLUEPRINT_INVALID` | matcher compile failed; dry-run is not attempted |

---

## Step 6 — POST revert (IN_REVIEW → DRAFT)

```bash
curl -s -X POST $AUTH \
  -H "Content-Type: application/json" \
  "$BASE/v1/authoring/drafts/$DRAFT/revert" \
  -d '{"reason": "smoke complete — back to editing"}' | jq .
```

**Expected:** `200 OK`

| Field | Expected |
|-------|----------|
| `current_state` | `"DRAFT"` |
| `transitions[-1].to_state` | `"DRAFT"` |
| `transitions[-1].dry_run_result_hash` | `null` (revert skips dry-run) |

---

## Step 7 — Approve (IN_REVIEW → APPROVED)

Requires a **distinct** principal with `authoring:approve`. The author cannot approve their own draft.

```bash
curl -s -X POST -H "X-Dev-Subject:priya-reviewer" -H "X-Dev-Tenant:$TENANT" \
  -H "Content-Type: application/json" \
  "$BASE/v1/authoring/drafts/$DRAFT/approve" \
  -d '{"reason": "formative sign-off"}' | jq .
```

**Expected:** `200 OK`

| Field | Expected |
|-------|----------|
| `current_state` | `"APPROVED"` |
| `transitions[-1].from_state` | `"IN_REVIEW"` |
| `transitions[-1].to_state` | `"APPROVED"` |
| `transitions[-1].actor_subject_id` | `priya-reviewer` |
| `transitions[-1].content_hash_snapshot` | matches draft `content_hash` at approve |

### Approver-distinct fires before later-phase gates

```bash
curl -s -X POST $AUTH \
  -H "Content-Type: application/json" \
  "$BASE/v1/authoring/drafts/$DRAFT/approve" \
  -d '{}' | jq .
```

**Expected:** `403` with `detail.error == "AUTHOR_CANNOT_APPROVE"` — not a 501. The invariant is live even for transitions that are not yet implemented (publish is still `PhaseNotImplementedError("phase C")`).

---

## Regression harness

The same sequence is automated in:

`services/pratibimb/tests/test_authoring_ramesh_e2e.py`

Run:

```bash
pytest services/pratibimb/tests/test_authoring_ramesh_e2e.py -v
```

When green, every future authoring change must keep this path working. Phase C (summative two-approver + publish) extends this file; it must not go red here.
