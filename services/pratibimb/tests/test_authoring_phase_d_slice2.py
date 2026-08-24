"""Phase D slice 2: publish preconditions + store.publish_draft()."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
]

from services.pratibimb.app.eval.rubric import Axis, Severity
from services.pratibimb.authoring.constants import (
    HARNESS_VERSION,
    CaseWorkflowState,
    FixtureKind,
    PublishPreconditionCode,
    RetireReasonCode,
    SCOPE_APPROVE,
    SCOPE_APPROVE_SUMMATIVE,
)
from services.pratibimb.authoring.errors import (
    ApproverCannotPublishError,
    DryRunHashMismatchError,
    InvalidStateTransitionError,
    PublishPreconditionFailed,
    VersionStringBurnedError,
)
from services.pratibimb.authoring.models import PublishedCaseVersionRow
from services.pratibimb.authoring.publish import backfill_clinical_reviewer_if_needed
from services.pratibimb.authoring.state_machine import TransitionActor
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.tests.test_authoring_phase_a import (
    AUTHOR,
    REVIEWER,
    TENANT,
    _miss_trace,
    _pass_trace,
)
from services.pratibimb.tests.test_authoring_phase_c import REVIEWER_B, _submit

PUBLISHER = "publisher-5"
GUIDELINE_PIN = "acc-2024@sha256:" + ("a" * 64)


def _full_blueprint(*, assessment_mode: str = "formative", corpus_tier: str = "silver"):
    bp = {
        "identity": {
            "case_id": "C1",
            "version": "1.0.0",
            "title": "Test case",
            "corpus_tier": corpus_tier,
            "assessment_mode": assessment_mode,
        },
        "targeting": {
            "roles": ["gnm"],
            "competencies": ["cardiac.stemi"],
            "difficulty": 0.5,
            "prerequisites": [],
        },
        "patient": {
            "demographics": {
                "name": "Test Patient",
                "age": 47,
                "sex": "M",
                "occupation": "driver",
                "state": "Maharashtra",
                "city": "Nagpur",
                "native_language": "mr",
                "education_years": 5,
                "monthly_income_inr": 14000,
            },
            "language": "mr",
            "voice_profile": "default",
            "literacy_years": 5,
            "persona_notes": "",
        },
        "clinical_truth": {
            "primary_diagnosis": "Inferior wall STEMI",
            "icd10": "I21.19",
            "differentials": [],
            "symptoms_present": ["chest pain"],
            "findings_present": [],
            "absent_findings": [],
            "allergies": [],
            "medications": [],
            "onset_minutes_ago": 40,
        },
        "environment": {
            "setting": "opd",
            "resources": ["ecg"],
            "distractors": [],
            "difficulty_parameters": {"time_pressure_seconds": 900},
        },
        "physiology": {
            "engine_version": "0.2.0",
            "initial_vitals": {
                "hr": 104,
                "sbp": 148,
                "dbp": 92,
                "spo2": 96,
                "rr": 20,
                "temp_c": 36.9,
            },
            "deterioration_rules": [],
            "pharmacology_profile": "default",
        },
        "interaction": {
            "allowed_disclosures": ["chest pain"],
            "hidden_facts": [],
            "family_personas": [],
            "dialogue_constraints": [],
        },
        "provenance": {
            "author": "unknown",
            "clinical_reviewer": None,
            "sources": [],
            "guideline_versions": [GUIDELINE_PIN],
            "reviewed_at": None,
            "content_hash": "",
        },
        "schema_version": "2.0.0",
        "grading_blueprint": {
            "case_id": "C1",
            "case_version": "1.0.0",
            "rubric_version": "0.3.0",
            "hits": [
                {
                    "id": "aspirin",
                    "axis": Axis.ACTION.value,
                    "matcher": "drug_given",
                    "params": {"drug_id": "aspirin"},
                    "points": 10.0,
                    "severity": Severity.MAJOR.value,
                    "required": True,
                    "fail_case_on_violation": True,
                }
            ],
        },
    }
    bp["grading_blueprint"]["hits"].append(
        {
            "id": "safety.check",
            "axis": Axis.SAFETY.value,
            "matcher": "drug_given",
            "params": {"drug_id": "aspirin"},
            "points": 5.0,
            "severity": Severity.CRITICAL.value,
            "required": True,
            "fail_case_on_violation": True,
        }
    )
    return bp


def _seed_full_draft(store: CaseDraftStore, *, assessment_mode: str = "formative"):
    draft = store.create_draft(
        tenant_id=TENANT,
        author_subject_id=AUTHOR,
        blueprint_json=_full_blueprint(assessment_mode=assessment_mode),
        blueprint_version="1.0.0",
    )
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.PERFECT_PATH,
        trace_json=_pass_trace(),
        expected_grade_json={"passed": True},
    )
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.CRITICAL_MISS,
        trace_json=_miss_trace(),
        expected_grade_json={"passed": False},
    )
    return draft


def _approve(store: CaseDraftStore, draft_id: str):
    return store.transition(
        draft_id,
        to_state=CaseWorkflowState.APPROVED,
        actor=TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE})),
    )


def _summative_grants(store: CaseDraftStore, draft_id: str):
    first = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}))
    second = TransitionActor(subject_id=REVIEWER_B, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}))
    store.record_summative_approval(draft_id, actor=first)
    store.record_summative_approval(draft_id, actor=second)


def test_publish_happy_path_formative(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="formative")
    _submit(store, draft.id)
    _approve(store, draft.id)
    result = store.publish_draft(
        draft.id,
        actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
    )
    authoring_session.commit()

    refreshed = store.get_draft(draft.id)
    assert refreshed.current_state == CaseWorkflowState.PUBLISHED.value
    assert result.published.content_hash == result.transition.content_hash_snapshot
    assert result.published.harness_version == HARNESS_VERSION
    assert result.published.assessment_mode == "formative"


def test_publish_happy_path_summative_with_two_grants(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="summative")
    bp = copy.deepcopy(draft.blueprint_json)
    bp["identity"]["corpus_tier"] = "gold"
    draft.blueprint_json = bp
    authoring_session.flush()

    _submit(store, draft.id)
    _summative_grants(store, draft.id)
    _approve(store, draft.id)

    result = store.publish_draft(
        draft.id,
        actor=TransitionActor(
            subject_id=PUBLISHER,
            scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
        ),
    )
    authoring_session.commit()

    assert result.published.clinical_reviewer == REVIEWER
    assert store.get_draft(draft.id).current_state == CaseWorkflowState.PUBLISHED.value


def test_publish_rejects_withdrawn_summative_before_publish(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="summative")
    bp = copy.deepcopy(draft.blueprint_json)
    bp["identity"]["corpus_tier"] = "gold"
    draft.blueprint_json = bp
    authoring_session.flush()

    _submit(store, draft.id)
    first = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}))
    second = TransitionActor(subject_id=REVIEWER_B, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}))
    g1 = store.record_summative_approval(draft.id, actor=first)
    store.record_summative_approval(draft.id, actor=second)
    store.withdraw_approval(draft.id, grant_id=g1.event.id, actor=first)
    _approve(store, draft.id)

    with pytest.raises(PublishPreconditionFailed) as exc:
        store.publish_draft(
            draft.id,
            actor=TransitionActor(
                subject_id=PUBLISHER,
                scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
            ),
        )
    assert exc.value.precondition_code == PublishPreconditionCode.SUMMATIVE_NOT_READY


def test_publish_rejects_compile_at_publish_with_pinned_snapshot(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="formative")
    _submit(store, draft.id)
    _approve(store, draft.id)

    bp = copy.deepcopy(draft.blueprint_json)
    bp["grading_blueprint"]["hits"].append(
        {
            "id": "bad.action",
            "axis": Axis.ACTION.value,
            "matcher": "action_within",
            "params": {
                "action_id": "nonexistent_action_xyz",
                "within_s": 60,
                "inner_hit": "aspirin",
            },
            "points": 1.0,
            "severity": Severity.MAJOR.value,
            "required": True,
            "fail_case_on_violation": True,
        }
    )
    draft.blueprint_json = bp
    authoring_session.flush()

    with pytest.raises(PublishPreconditionFailed) as exc:
        store.publish_draft(
            draft.id,
            actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
        )
    assert exc.value.precondition_code == PublishPreconditionCode.COMPILE_FAILED


def test_publish_rejects_dry_run_hash_mismatch_with_phase_publish(store, authoring_session, monkeypatch):
    draft = _seed_full_draft(store, assessment_mode="formative")
    _submit(store, draft.id)
    _approve(store, draft.id)

    from services.pratibimb.authoring.constants import FixtureKind
    from services.pratibimb.authoring.dry_run import DryRunResult, FixtureDryRunOutcome

    monkeypatch.setattr(
        "services.pratibimb.authoring.state_machine.run_dry_run",
        lambda blueprint_json, fixtures: DryRunResult(
            outcomes=(
                FixtureDryRunOutcome(
                    fixture_kind=FixtureKind.PERFECT_PATH,
                    expected_passed=True,
                    actual_passed=True,
                ),
                FixtureDryRunOutcome(
                    fixture_kind=FixtureKind.CRITICAL_MISS,
                    expected_passed=False,
                    actual_passed=False,
                ),
            ),
            blueprint_hash="x" * 64,
            fixture_hashes=("a", "b"),
        ),
    )

    with pytest.raises(DryRunHashMismatchError) as exc:
        store.publish_draft(
            draft.id,
            actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
        )
    assert exc.value.phase == "publish"


def test_publish_rejects_author_as_publisher(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="formative")
    _submit(store, draft.id)
    _approve(store, draft.id)

    with pytest.raises(PublishPreconditionFailed) as exc:
        store.publish_draft(
            draft.id,
            actor=TransitionActor(subject_id=AUTHOR, scopes=frozenset({SCOPE_APPROVE})),
        )
    assert exc.value.precondition_code == PublishPreconditionCode.AUTHOR_CANNOT_PUBLISH


def test_publish_rejects_unpinned_guidelines_for_summative(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="summative")
    bp = copy.deepcopy(draft.blueprint_json)
    bp["identity"]["corpus_tier"] = "gold"
    bp["provenance"]["guideline_versions"] = ["acc-2024-v1"]
    draft.blueprint_json = bp
    authoring_session.flush()

    _submit(store, draft.id)
    _summative_grants(store, draft.id)
    _approve(store, draft.id)

    with pytest.raises(PublishPreconditionFailed) as exc:
        store.publish_draft(
            draft.id,
            actor=TransitionActor(
                subject_id=PUBLISHER,
                scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
            ),
        )
    assert exc.value.precondition_code == PublishPreconditionCode.GUIDELINE_NOT_PINNED


def test_publish_rejects_from_in_review_even_when_summative_ready(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="summative")
    bp = copy.deepcopy(draft.blueprint_json)
    bp["identity"]["corpus_tier"] = "gold"
    draft.blueprint_json = bp
    authoring_session.flush()

    _submit(store, draft.id)
    _summative_grants(store, draft.id)

    with pytest.raises(InvalidStateTransitionError):
        store.publish_draft(
            draft.id,
            actor=TransitionActor(
                subject_id=PUBLISHER,
                scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
            ),
        )


def test_version_string_burn_rejects_republish_after_retire(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="formative")
    case_id = draft.blueprint_json["identity"]["case_id"]
    version = draft.blueprint_json["identity"]["version"]
    authoring_session.add(
        PublishedCaseVersionRow(
            id=str(uuid.uuid4()),
            draft_id=draft.id,
            tenant_id=draft.tenant_id,
            case_id=case_id,
            version=version,
            assessment_mode="formative",
            content_hash="a" * 64,
            harness_version=HARNESS_VERSION,
            published_at=datetime.now(timezone.utc),
            published_by=REVIEWER,
            retired_at=datetime.now(timezone.utc),
            retired_by=REVIEWER,
            retired_reason_code=RetireReasonCode.SUPERSEDED_BY_NEW_VERSION.value,
        )
    )
    authoring_session.flush()

    draft.current_state = CaseWorkflowState.APPROVED.value
    authoring_session.flush()

    with pytest.raises(VersionStringBurnedError):
        store.publish_draft(
            draft.id,
            actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
        )


def test_four_principal_summative_publish_success_and_collision_rejected(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="summative")
    bp = copy.deepcopy(draft.blueprint_json)
    bp["identity"]["corpus_tier"] = "gold"
    draft.blueprint_json = bp
    authoring_session.flush()

    _submit(store, draft.id)
    _summative_grants(store, draft.id)
    _approve(store, draft.id)

    store.publish_draft(
        draft.id,
        actor=TransitionActor(
            subject_id=PUBLISHER,
            scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
        ),
    )
    authoring_session.commit()

    draft2 = _seed_full_draft(store, assessment_mode="summative")
    bp2 = copy.deepcopy(draft2.blueprint_json)
    bp2["identity"]["corpus_tier"] = "gold"
    bp2["identity"]["version"] = "2.0.0"
    draft2.blueprint_json = bp2
    authoring_session.flush()
    _submit(store, draft2.id)
    _summative_grants(store, draft2.id)
    _approve(store, draft2.id)

    with pytest.raises(ApproverCannotPublishError):
        store.publish_draft(
            draft2.id,
            actor=TransitionActor(
                subject_id=REVIEWER_B,
                scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
            ),
        )


def test_clinical_reviewer_backfill_at_publish_is_idempotent(store, authoring_session):
    draft = _seed_full_draft(store, assessment_mode="summative")
    bp = copy.deepcopy(draft.blueprint_json)
    bp["identity"]["corpus_tier"] = "gold"
    draft.blueprint_json = bp
    authoring_session.flush()

    _submit(store, draft.id)
    _summative_grants(store, draft.id)
    _approve(store, draft.id)

    events = store.list_approval_events(draft.id)
    assert draft.blueprint_json["provenance"]["clinical_reviewer"] is None
    assert backfill_clinical_reviewer_if_needed(draft, events) is True
    first_reviewed_at = draft.blueprint_json["provenance"]["reviewed_at"]
    assert draft.blueprint_json["provenance"]["clinical_reviewer"] == REVIEWER
    assert backfill_clinical_reviewer_if_needed(draft, events) is False
    assert draft.blueprint_json["provenance"]["reviewed_at"] == first_reviewed_at

    result = store.publish_draft(
        draft.id,
        actor=TransitionActor(
            subject_id=PUBLISHER,
            scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
        ),
    )
    authoring_session.commit()
    assert result.published.clinical_reviewer == REVIEWER


def test_version_chain_two_hashes_queryable(store, authoring_session):
    draft_v1 = _seed_full_draft(store, assessment_mode="formative")
    _submit(store, draft_v1.id)
    _approve(store, draft_v1.id)
    pub_v1 = store.publish_draft(
        draft_v1.id,
        actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
    )

    draft_v2 = _seed_full_draft(store, assessment_mode="formative")
    bp = copy.deepcopy(draft_v2.blueprint_json)
    bp["identity"]["version"] = "2.0.0"
    bp["grading_blueprint"]["hits"][0]["points"] = 11.0
    draft_v2.blueprint_json = bp
    authoring_session.flush()
    _submit(store, draft_v2.id)
    _approve(store, draft_v2.id)
    pub_v2 = store.publish_draft(
        draft_v2.id,
        actor=TransitionActor(subject_id=PUBLISHER, scopes=frozenset({SCOPE_APPROVE})),
    )
    authoring_session.commit()

    rows = store.list_published_versions_for_case("C1")
    hashes = {row.content_hash for row in rows}
    assert pub_v1.published.content_hash in hashes
    assert pub_v2.published.content_hash in hashes
    assert pub_v1.published.content_hash != pub_v2.published.content_hash
