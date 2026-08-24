"""Phase D publish helpers — precondition checks and provenance backfill."""
from __future__ import annotations

import re
from datetime import datetime

from shared.schemas.case_v2 import CorpusTier
from shared.schemas.grading import Axis
from shared.schemas.session import AssessmentMode

from services.pratibimb.authoring.blueprint_io import blueprint_from_case_json
from services.pratibimb.authoring.compiler import CompilerErrors, compile_blueprint
from services.pratibimb.authoring.constants import (
    REQUIRED_FIXTURE_KINDS,
    SCOPE_APPROVE,
    SCOPE_APPROVE_SUMMATIVE,
    ApprovalEventType,
    ApprovalKind,
    FixtureKind,
)
from services.pratibimb.authoring.dry_run import DryRunResult
from services.pratibimb.authoring.models import (
    ApprovalEventRow,
    CaseDraftRow,
    GoldenFixtureRow,
    PublishedCaseVersionRow,
)
from services.pratibimb.authoring.registry_snapshot import RegistrySnapshot

GUIDELINE_PIN_RE = re.compile(r"^[^@]+@sha256:[0-9a-f]{64}$")
SUMMATIVE_REQUIRED_AXES = frozenset({Axis.SAFETY.value})


def assessment_mode_from_blueprint(blueprint_json: dict) -> AssessmentMode:
    identity = blueprint_json.get("identity") or {}
    raw = str(identity.get("assessment_mode") or AssessmentMode.PRACTICE.value)
    return AssessmentMode(raw)


def required_publish_scope(mode: AssessmentMode) -> str:
    if mode is AssessmentMode.SUMMATIVE:
        return SCOPE_APPROVE_SUMMATIVE
    return SCOPE_APPROVE


def case_identity_from_draft(draft: CaseDraftRow) -> tuple[str, str]:
    identity = draft.blueprint_json.get("identity") or {}
    return str(identity.get("case_id") or ""), str(identity.get("version") or draft.blueprint_version)


def first_active_summative_grant(events: list[ApprovalEventRow]) -> ApprovalEventRow | None:
    from services.pratibimb.authoring.state_machine import active_approval_subject_ids

    active = active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE)
    grants = [
        row
        for row in events
        if row.event_type == ApprovalEventType.GRANT.value
        and row.kind == ApprovalKind.SUMMATIVE.value
        and row.actor_subject_id in active
    ]
    if not grants:
        return None
    return min(grants, key=lambda row: row.occurred_at)


def backfill_clinical_reviewer_if_needed(
    draft: CaseDraftRow,
    events: list[ApprovalEventRow],
) -> bool:
    """
    Idempotent provenance backfill from first active summative GRANT.

    Returns True when a write was applied; skip-if-populated survives retries.
    The null-check is load-bearing: publish may fail a later precondition and
    retry; a populated clinical_reviewer (including a human edit between
    attempts) must not be stomped by a second backfill.
    """
    blueprint = dict(draft.blueprint_json)
    provenance = dict(blueprint.get("provenance") or {})
    if provenance.get("clinical_reviewer"):
        return False

    grant = first_active_summative_grant(events)
    if grant is None:
        return False

    reviewed_at = grant.occurred_at
    if isinstance(reviewed_at, datetime):
        provenance["reviewed_at"] = reviewed_at.isoformat()
    else:
        provenance["reviewed_at"] = str(reviewed_at)
    provenance["clinical_reviewer"] = grant.actor_subject_id
    blueprint["provenance"] = provenance
    draft.blueprint_json = blueprint
    return True


def guideline_versions_pinned(blueprint_json: dict) -> bool:
    provenance = blueprint_json.get("provenance") or {}
    guidelines = provenance.get("guideline_versions") or ()
    if not guidelines:
        return False
    return all(GUIDELINE_PIN_RE.match(str(entry)) for entry in guidelines)


def summative_axes_covered(blueprint_json: dict) -> bool:
    hits = (blueprint_json.get("grading_blueprint") or {}).get("hits") or ()
    axes = {str(hit.get("axis")) for hit in hits if hit.get("axis")}
    return SUMMATIVE_REQUIRED_AXES.issubset(axes)


def compile_at_publish(
    blueprint_json: dict,
    *,
    snapshot: RegistrySnapshot | None,
) -> CompilerErrors | None:
    result = compile_blueprint(blueprint_json, snapshot=snapshot)
    if isinstance(result, CompilerErrors):
        return result
    return None


def fresh_dry_run_at_publish(
    draft: CaseDraftRow,
    fixtures: list[GoldenFixtureRow],
    *,
    snapshot: RegistrySnapshot | None,
) -> DryRunResult:
    from services.pratibimb.authoring.state_machine import execute_draft_to_in_review

    errors = compile_at_publish(draft.blueprint_json, snapshot=snapshot)
    if errors is not None:
        raise RuntimeError("compile_at_publish must pass before fresh dry run")
    return execute_draft_to_in_review(draft, fixtures)


def fixture_hashes(fixtures: list[GoldenFixtureRow]) -> dict[str, str]:
    by_kind = {FixtureKind(row.fixture_kind): row.content_hash for row in fixtures}
    missing = REQUIRED_FIXTURE_KINDS - set(by_kind)
    if missing:
        from services.pratibimb.authoring.errors import MissingFixturesError

        raise MissingFixturesError(
            f"missing golden fixtures: {', '.join(sorted(k.value for k in missing))}"
        )
    return {kind.value: by_kind[kind] for kind in REQUIRED_FIXTURE_KINDS}


def assert_tenant_consistent_for_case(
    draft: CaseDraftRow,
    prior_rows: list[PublishedCaseVersionRow],
) -> None:
    for row in prior_rows:
        if row.case_id == case_identity_from_draft(draft)[0] and row.tenant_id != draft.tenant_id:
            raise ValueError("tenant mismatch")


def version_string_burned(
    *,
    tenant_id: str,
    case_id: str,
    version: str,
    prior_rows: list[PublishedCaseVersionRow],
) -> bool:
    return any(
        row.tenant_id == tenant_id and row.case_id == case_id and row.version == version
        for row in prior_rows
    )


def structural_validation_errors(blueprint_json: dict) -> list[str]:
    return blueprint_from_case_json(blueprint_json).validation_errors()
