# Authoring harness — Phase D (publish / retire)

Status: implemented through Slice 4 (read surface + metrics).  
Parent: [`authoring_harness.md`](./authoring_harness.md).

## 1. Transitions

| Edge | Scope | Distinct-principal rule |
|---|---|---|
| `APPROVED → PUBLISHED` (formative) | `authoring:approve` | Publisher ≠ author |
| `APPROVED → PUBLISHED` (summative) | `authoring:approve_summative` | Publisher ≠ author ≠ approver_1 ≠ approver_2 |
| `PUBLISHED → RETIRED` | Same as corresponding publish | Retirer ≠ publisher |

Retirement uses the **publish bar**, not `authoring:admin`. Retirement is content-lifecycle, not system administration; the qualification bar matches the publish bar it reverses. `authoring:admin` stays reserved for tenant/registry-level operations.

Version strings are permanently burned: `UNIQUE (tenant_id, case_id, version)` with no soft-delete carve-out. Republish after retire requires a new version string.

## 2. Storage

- `published_case_versions` — append-only publish freeze; retirement is a tombstone patch (`retired_at`, `retired_by`, `retired_reason_code`, `retired_reason_text`).
- `retired_case_versions` — append-only audit; source of truth for "when and why" alongside the state transition.

`retire_published()` is a single flush: state → `RETIRED` + tombstone + audit insert. Partial retirement is forbidden.

## 3. HTTP (Phase D)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/authoring/drafts/{id}/publish` | Empty body; publisher from JWT |
| `POST` | `/v1/authoring/drafts/{id}/retire` | `{reason_code, reason_note?}` |
| `GET` | `/v1/authoring/published/{case_id}/{version}` | 200 even when retired (not deletion) |
| `GET` | `/v1/authoring/published?case_id=` | `include_retired` (default true) |
| `GET` | `/v1/authoring/drafts/{id}` | Transitions ordered by `occurred_at`; includes published/retired views when present |

## 4. Retire reasons

Closed enum: `clinical_error`, `superseded_by_new_version`, `guideline_change`, `policy_change`, `operator_request`.  
`reason_note` optional, max 256 chars.

## 5. Metrics

| Name | Type | Labels |
|---|---|---|
| `authoring_publish_total` | counter | `tenant`, `tier` |
| `authoring_retire_total` | counter | `tenant`, `reason_code` |
| `authoring_published_active_total` | gauge | `tenant` |
| `authoring_retired_total` | gauge | `tenant` |

## 6. Reserved ledger-read `query_kind` placeholders

Registered in `KNOWN_QUERY_KINDS` only (not emitted until Phase E):

- `get_published_case_versions`
- `get_retirement_history`

## 7. Out of scope (downstream)

Un-retire, retirement notifications, retention/deletion of retired rows, learner-facing "this case is retired" surface, runtime corpus write, ledger `content_hash` wiring for published rows.
