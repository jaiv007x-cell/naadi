"""Phase D slice 1: publish storage + schema guards (no publish logic)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytest_plugins = ["services.pratibimb.tests.test_authoring_phase_a"]

from services.pratibimb.authoring.constants import (
    HARNESS_VERSION,
    CaseWorkflowState,
    PolicyRejectionCode,
    PublishPreconditionCode,
    RetireReasonCode,
    SCOPE_APPROVE_SUMMATIVE,
)
from services.pratibimb.authoring.errors import InvalidStateTransitionError
from services.pratibimb.authoring.models import PublishedCaseVersionRow
from services.pratibimb.authoring.state_machine import (
    IMPLEMENTED_TRANSITIONS,
    TransitionActor,
    validate_publish_preconditions,
    validate_retire_preconditions,
)
from services.pratibimb.tests.test_authoring_phase_a import (
    REVIEWER,
    _seed_draft_with_fixtures,
)


def _now():
    return datetime.now(timezone.utc)


def _case_id_from_draft(draft):
    return str(draft.blueprint_json.get("identity", {}).get("case_id") or "")


def _published_row(draft, *, version: str, content_hash: str, retired_at=None):
    return PublishedCaseVersionRow(
        id=str(uuid.uuid4()),
        draft_id=draft.id,
        tenant_id=draft.tenant_id,
        case_id=_case_id_from_draft(draft),
        version=version,
        assessment_mode="summative",
        content_hash=content_hash,
        harness_version=HARNESS_VERSION,
        published_at=_now(),
        published_by=REVIEWER,
        retired_at=retired_at,
        retired_by=REVIEWER if retired_at else None,
        retired_reason_code=(
            RetireReasonCode.POLICY_CHANGE.value if retired_at else None
        ),
    )


def test_implemented_transitions_includes_publish_and_retire_only():
    assert (CaseWorkflowState.APPROVED, CaseWorkflowState.PUBLISHED) in IMPLEMENTED_TRANSITIONS
    assert (CaseWorkflowState.PUBLISHED, CaseWorkflowState.RETIRED) in IMPLEMENTED_TRANSITIONS
    assert (CaseWorkflowState.IN_REVIEW, CaseWorkflowState.PUBLISHED) not in IMPLEMENTED_TRANSITIONS
    assert len(IMPLEMENTED_TRANSITIONS) == 5


def test_unique_constraint_rejects_duplicate_active_row(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    authoring_session.add(_published_row(draft, version="v3", content_hash="a" * 64))
    authoring_session.flush()
    authoring_session.add(_published_row(draft, version="v3", content_hash="b" * 64))
    with pytest.raises(IntegrityError):
        authoring_session.flush()
    authoring_session.rollback()


def test_unique_constraint_rejects_duplicate_even_when_first_retired(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    authoring_session.add(
        _published_row(
            draft,
            version="v3",
            content_hash="a" * 64,
            retired_at=_now(),
        )
    )
    authoring_session.flush()
    authoring_session.add(_published_row(draft, version="v3", content_hash="b" * 64))
    with pytest.raises(IntegrityError):
        authoring_session.flush()
    authoring_session.rollback()


def test_raw_sql_duplicate_published_case_version_constraint_fires(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    case_id = _case_id_from_draft(draft)

    authoring_session.add(_published_row(draft, version="v3", content_hash="a" * 64))
    authoring_session.commit()

    insert = text(
        """
        INSERT INTO authoring.published_case_versions
            (id, draft_id, tenant_id, case_id, version, assessment_mode, content_hash,
             harness_version, published_at, published_by)
        VALUES
            (:id, :draft_id, :tenant_id, :case_id, :version, :assessment_mode, :content_hash,
             :harness_version, :published_at, :published_by)
        """
    )

    with pytest.raises(IntegrityError):
        authoring_session.execute(
            insert,
            {
                "id": str(uuid.uuid4()),
                "draft_id": draft.id,
                "tenant_id": draft.tenant_id,
                "case_id": case_id,
                "version": "v3",
                "assessment_mode": "summative",
                "content_hash": "c" * 64,
                "harness_version": HARNESS_VERSION,
                "published_at": _now(),
                "published_by": REVIEWER,
            },
        )
        authoring_session.commit()

    authoring_session.rollback()


def test_phase_d_policy_and_subcodes_are_wire_contract():
    assert PolicyRejectionCode.ALREADY_PUBLISHED.value == "ALREADY_PUBLISHED"
    assert PolicyRejectionCode.VERSION_STRING_BURNED.value == "VERSION_STRING_BURNED"
    assert PolicyRejectionCode.CANNOT_PUBLISH_RETIRED.value == "CANNOT_PUBLISH_RETIRED"
    assert (
        PolicyRejectionCode.RETIRE_REQUIRES_PUBLISHED_STATE.value
        == "RETIRE_REQUIRES_PUBLISHED_STATE"
    )
    assert PolicyRejectionCode.PUBLISH_PRECONDITION_FAILED.value == "PUBLISH_PRECONDITION_FAILED"
    assert PolicyRejectionCode.APPROVER_CANNOT_PUBLISH.value == "APPROVER_CANNOT_PUBLISH"

    assert PublishPreconditionCode.WRONG_STATE.value == "WRONG_STATE"
    assert PublishPreconditionCode.DRY_RUN_HASH_MISMATCH.value == "DRY_RUN_HASH_MISMATCH"
    assert RetireReasonCode.POLICY_CHANGE.value == "policy_change"
    assert RetireReasonCode.GUIDELINE_CHANGE.value == "guideline_change"


def test_publish_preconditions_wrong_state_guard_reads_implemented_transitions(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    actor = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}))

    for bad_state in (
        CaseWorkflowState.DRAFT,
        CaseWorkflowState.IN_REVIEW,
        CaseWorkflowState.PUBLISHED,
    ):
        draft.current_state = bad_state.value
        authoring_session.flush()
        with pytest.raises(InvalidStateTransitionError) as exc:
            validate_publish_preconditions(
                draft,
                actor=actor,
                events=[],
                fixtures=[],
                pinned_snapshot=None,
                submit_dry_run_hash_value=None,
                prior_publishes=[],
            )
        assert exc.value.code == PublishPreconditionCode.WRONG_STATE.value
        assert exc.value.to_state == CaseWorkflowState.PUBLISHED.value


def test_retire_preconditions_wrong_state_guard_reads_implemented_transitions(store, authoring_session):
    from services.pratibimb.authoring.constants import SCOPE_APPROVE
    from services.pratibimb.authoring.errors import RetirePreconditionFailed
    from services.pratibimb.authoring.constants import RetirePreconditionCode

    draft = _seed_draft_with_fixtures(store)
    actor = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE}))

    for bad_state in (
        CaseWorkflowState.DRAFT,
        CaseWorkflowState.IN_REVIEW,
        CaseWorkflowState.APPROVED,
    ):
        draft.current_state = bad_state.value
        authoring_session.flush()
        with pytest.raises(InvalidStateTransitionError) as exc:
            validate_retire_preconditions(
                draft,
                actor=actor,
                published=None,
                reason_code=RetireReasonCode.CLINICAL_ERROR.value,
            )
        assert exc.value.code == PublishPreconditionCode.WRONG_STATE.value
        assert exc.value.to_state == CaseWorkflowState.RETIRED.value

    draft.current_state = CaseWorkflowState.PUBLISHED.value
    authoring_session.flush()
    with pytest.raises(RetirePreconditionFailed) as exc:
        validate_retire_preconditions(
            draft,
            actor=actor,
            published=None,
            reason_code=RetireReasonCode.CLINICAL_ERROR.value,
        )
    assert exc.value.precondition_code == RetirePreconditionCode.NO_PUBLISHED_ROW.value
