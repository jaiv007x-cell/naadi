"""Phase C: approval workflow, distinct principals, append-only, hash recheck."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

pytest_plugins = ["services.pratibimb.tests.test_authoring_phase_a"]

from services.pratibimb.authoring.constants import (
    AUTHORING_SCHEMA,
    SCOPE_APPROVE,
    SCOPE_APPROVE_SUMMATIVE,
    SCOPE_SUBMIT,
    ApprovalEventType,
    ApprovalKind,
    CaseWorkflowState,
    CompileIssueCode,
    PolicyRejectionCode,
    SummativeGrantOutcome,
)
from services.pratibimb.authoring.compiler import CompilerErrors, compile_blueprint
from services.pratibimb.authoring.errors import (
    AuthorCannotApproveError,
    DryRunHashMismatchError,
    DuplicateApproverError,
    ScopeDeniedError,
)
from services.pratibimb.authoring.state_machine import (
    TransitionActor,
    active_approval_subject_ids,
    summative_ready,
)
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.tests.test_authoring_phase_a import (
    AUTHOR,
    REVIEWER,
    _seed_draft_with_fixtures,
)

REVIEWER_B = "reviewer-3"
REVIEWER_C = "reviewer-4"


def _submit(store: CaseDraftStore, draft_id: str):
    return store.transition(
        draft_id,
        to_state=CaseWorkflowState.IN_REVIEW,
        actor=TransitionActor(subject_id=AUTHOR, scopes=frozenset({SCOPE_SUBMIT})),
    )


def test_formative_approve_requires_approve_not_summative_scope(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    actor = TransitionActor(
        subject_id=REVIEWER,
        scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
    )
    with pytest.raises(ScopeDeniedError, match="authoring:approve"):
        store.transition(
            draft.id,
            to_state=CaseWorkflowState.APPROVED,
            actor=actor,
        )


def test_formative_approve_records_append_only_grant(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    submit = _submit(store, draft.id)
    actor = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE}))
    transition = store.transition(
        draft.id,
        to_state=CaseWorkflowState.APPROVED,
        actor=actor,
        reason="formative sign-off",
    )
    authoring_session.commit()

    events = store.list_approval_events(draft.id)
    assert len(events) == 1
    grant = events[0]
    assert grant.event_type == ApprovalEventType.GRANT.value
    assert grant.kind == ApprovalKind.FORMATIVE.value
    assert grant.actor_scope == SCOPE_APPROVE
    assert grant.actor_subject_id == REVIEWER
    assert grant.dry_run_result_hash == submit.dry_run_result_hash
    assert grant.submit_dry_run_hash == submit.dry_run_result_hash
    assert grant.validation_context_hash == submit.validation_context_hash
    assert transition.dry_run_result_hash == submit.dry_run_result_hash
    assert transition.content_hash_snapshot == submit.content_hash_snapshot
    assert transition.validation_context_hash == submit.validation_context_hash

    audit = store.list_audit_events(draft.id)
    assert any(a.to_state == CaseWorkflowState.APPROVED.value for a in audit)


def test_approval_refuses_when_dry_run_hash_drifted(store, authoring_session, monkeypatch):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    monkeypatch.setattr(
        "services.pratibimb.authoring.store.submit_dry_run_hash",
        lambda transitions: "0" * 64,
    )
    actor = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE}))
    with pytest.raises(DryRunHashMismatchError) as exc:
        store.transition(
            draft.id,
            to_state=CaseWorkflowState.APPROVED,
            actor=actor,
        )
    assert exc.value.code == PolicyRejectionCode.DRY_RUN_HASH_MISMATCH
    assert store.get_draft(draft.id).current_state == CaseWorkflowState.IN_REVIEW.value
    assert store.list_approval_events(draft.id) == []


def test_summative_rejects_formative_scope(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    actor = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE}))
    with pytest.raises(ScopeDeniedError, match="authoring:approve_summative"):
        store.record_summative_approval(draft.id, actor=actor)


def test_summative_rejects_author_as_first_approver(store, authoring_session):
    """Author as approver_1 must fail — not only the second slot."""
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    actor = TransitionActor(
        subject_id=AUTHOR,
        scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
    )
    with pytest.raises(AuthorCannotApproveError):
        store.record_summative_approval(draft.id, actor=actor)


def test_summative_rejects_author_as_second_approver(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    first = TransitionActor(
        subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    store.record_summative_approval(draft.id, actor=first)
    author_actor = TransitionActor(
        subject_id=AUTHOR,
        scopes=frozenset({SCOPE_APPROVE_SUMMATIVE}),
    )
    with pytest.raises(AuthorCannotApproveError):
        store.record_summative_approval(draft.id, actor=author_actor)


def test_summative_two_distinct_principals(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    submit = _submit(store, draft.id)
    first = TransitionActor(
        subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    second = TransitionActor(
        subject_id=REVIEWER_B, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    store.record_summative_approval(draft.id, actor=first, reason="first")
    second_result = store.record_summative_approval(draft.id, actor=second, reason="second")
    authoring_session.commit()

    events = store.list_approval_events(draft.id)
    grants = [e for e in events if e.event_type == ApprovalEventType.GRANT.value]
    assert len(grants) == 2
    assert grants[0].actor_subject_id != grants[1].actor_subject_id
    assert {g.actor_subject_id for g in grants} == {REVIEWER, REVIEWER_B}
    assert all(g.dry_run_result_hash == submit.dry_run_result_hash for g in grants)
    assert store.get_draft(draft.id).current_state == CaseWorkflowState.IN_REVIEW.value
    assert summative_ready(events) is True
    assert second_result.outcome is SummativeGrantOutcome.GRANT_RECORDED
    assert second_result.summative_ready is True


def test_summative_same_principal_twice_rejected(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    actor = TransitionActor(
        subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    store.record_summative_approval(draft.id, actor=actor)
    with pytest.raises(DuplicateApproverError, match="approver_1_subject_id"):
        store.record_summative_approval(draft.id, actor=actor)
    events = store.list_approval_events(draft.id)
    assert len(active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE)) == 1


def test_withdraw_appends_does_not_delete_grant(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    actor = TransitionActor(
        subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    grant = store.record_summative_approval(draft.id, actor=actor)
    grant_id = grant.event.id
    withdraw = store.withdraw_approval(
        draft.id, grant_id=grant_id, actor=actor, reason="changed mind"
    )
    authoring_session.commit()

    events = store.list_approval_events(draft.id)
    assert len(events) == 2
    assert events[0].id == grant_id
    assert events[0].event_type == ApprovalEventType.GRANT.value
    assert withdraw.event_type == ApprovalEventType.WITHDRAW.value
    assert withdraw.supersedes_event_id == grant_id
    assert active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE) == frozenset()

    still = authoring_session.get(type(grant.event), grant_id)
    assert still is not None
    assert still.event_type == ApprovalEventType.GRANT.value


def test_withdraw_allows_rerecord_by_same_principal(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    actor = TransitionActor(
        subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    grant = store.record_summative_approval(draft.id, actor=actor)
    store.withdraw_approval(draft.id, grant_id=grant.event.id, actor=actor)
    again = store.record_summative_approval(draft.id, actor=actor, reason="re-approve")
    authoring_session.commit()
    events = store.list_approval_events(draft.id)
    assert again.event.id != grant.event.id
    assert active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE) == frozenset(
        {REVIEWER}
    )


def test_content_hash_drift_blocks_approve(store, authoring_session):
    from services.pratibimb.authoring.errors import ContentHashDriftError

    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    tampered = dict(draft.blueprint_json)
    tampered["_tamper"] = "1"
    draft.blueprint_json = tampered
    authoring_session.flush()

    actor = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE}))
    with pytest.raises(ContentHashDriftError) as exc:
        store.transition(draft.id, to_state=CaseWorkflowState.APPROVED, actor=actor)
    assert exc.value.code == PolicyRejectionCode.CONTENT_HASH_DRIFT
    assert store.get_draft(draft.id).current_state == CaseWorkflowState.IN_REVIEW.value
    assert store.list_approval_events(draft.id) == []


def test_approve_compile_uses_submit_registry_snapshot(store, authoring_session, monkeypatch):
    draft = _seed_draft_with_fixtures(store)
    bp = dict(draft.blueprint_json)
    hits = list(bp["grading_blueprint"]["hits"])
    hits.append(
        {
            "id": "timing.known_action",
            "axis": "timing",
            "matcher": "action_within",
            "params": {
                "action_id": "give_aspirin_325_chewed",
                "within_s": 60,
                "inner_hit": "aspirin",
            },
            "points": 1.0,
        }
    )
    bp["grading_blueprint"] = {**bp["grading_blueprint"], "hits": hits}
    draft.blueprint_json = bp
    authoring_session.flush()
    _submit(store, draft.id)

    import services.pratibimb.app.action_registry as action_registry

    monkeypatch.setattr(action_registry, "KNOWN_ACTION_IDS", frozenset())
    actor = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE}))
    transition = store.transition(
        draft.id, to_state=CaseWorkflowState.APPROVED, actor=actor
    )
    submit = store.list_transitions(draft.id)[0]
    assert transition.content_hash_snapshot == submit.content_hash_snapshot
    assert transition.validation_context_hash == submit.validation_context_hash


def test_withdraw_flips_summative_ready_false(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    first = TransitionActor(
        subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    second = TransitionActor(
        subject_id=REVIEWER_B, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    g1 = store.record_summative_approval(draft.id, actor=first)
    store.record_summative_approval(draft.id, actor=second)
    events = store.list_approval_events(draft.id)
    assert summative_ready(events) is True

    store.withdraw_approval(
        draft.id, grant_id=g1.event.id, actor=first, reason="withdraw one"
    )
    authoring_session.commit()
    events = store.list_approval_events(draft.id)
    assert summative_ready(events) is False
    assert active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE) == frozenset(
        {REVIEWER_B}
    )


def test_third_summative_grant_recorded_no_state_change(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)
    actors = [
        TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})),
        TransitionActor(subject_id=REVIEWER_B, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})),
        TransitionActor(subject_id=REVIEWER_C, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})),
    ]
    store.record_summative_approval(draft.id, actor=actors[0])
    store.record_summative_approval(draft.id, actor=actors[1])
    third = store.record_summative_approval(draft.id, actor=actors[2], reason="extra voucher")
    authoring_session.commit()

    events = store.list_approval_events(draft.id)
    grants = [e for e in events if e.event_type == ApprovalEventType.GRANT.value]
    assert len(grants) == 3
    assert summative_ready(events) is True
    assert third.outcome is SummativeGrantOutcome.GRANT_RECORDED_NO_STATE_CHANGE
    assert third.summative_ready is True


def test_duplicate_summative_grant_raw_sql_fires_trigger(store, authoring_session):
    """Storage-layer guard — survives service-layer 'simplification'."""
    draft = _seed_draft_with_fixtures(store)
    submit = _submit(store, draft.id)
    actor = TransitionActor(
        subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE_SUMMATIVE})
    )
    grant = store.record_summative_approval(draft.id, actor=actor)
    authoring_session.commit()

    dup_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    sql = text(f"""
        INSERT INTO {AUTHORING_SCHEMA}.approval_events (
            id, draft_id, event_type, kind, actor_subject_id, actor_scope,
            dry_run_result_hash, submit_dry_run_hash, content_hash,
            validation_context_hash, reason, supersedes_event_id, occurred_at
        ) VALUES (
            :id, :draft_id, 'GRANT', 'SUMMATIVE', :actor, 'authoring:approve_summative',
            :dry_hash, :submit_hash, :content_hash,
            :ctx_hash, 'raw sql bypass', NULL, :occurred_at
        )
    """)
    with pytest.raises(Exception, match="duplicate summative approver"):
        authoring_session.execute(
            sql,
            {
                "id": dup_id,
                "draft_id": draft.id,
                "actor": REVIEWER,
                "dry_hash": submit.dry_run_result_hash,
                "submit_hash": submit.dry_run_result_hash,
                "content_hash": grant.event.content_hash,
                "ctx_hash": grant.event.validation_context_hash,
                "occurred_at": now,
            },
        )
        authoring_session.commit()


def test_retired_action_messages_use_pinned_snapshot_not_live(store, authoring_session, monkeypatch):
    import services.pratibimb.app.action_registry as action_registry

    # Submit-era catalog: no retirement dates published yet.
    monkeypatch.setattr(
        "services.pratibimb.authoring.registry_snapshot.DEPRECATED_ACTION_RETIRED_ON",
        {},
    )
    monkeypatch.setattr(action_registry, "DEPRECATED_ACTION_RETIRED_ON", {})

    draft = _seed_draft_with_fixtures(store)
    _submit(store, draft.id)

    # Live registry gains retirement dates after submit.
    monkeypatch.setitem(action_registry.DEPRECATED_ACTION_RETIRED_ON, "give_aspirin", "2099-01-01")
    pinned = store.pinned_registry_snapshot(draft.id)
    assert pinned is not None
    assert pinned.retired_on == {}

    bp = dict(draft.blueprint_json)
    hits = list(bp["grading_blueprint"]["hits"])
    hits.append(
        {
            "id": "timing.legacy_aspirin",
            "axis": "timing",
            "matcher": "action_within",
            "params": {
                "action_id": "give_aspirin",
                "within_s": 60,
                "inner_hit": "aspirin",
            },
            "points": 1.0,
        }
    )
    bp["grading_blueprint"] = {**bp["grading_blueprint"], "hits": hits}

    live = compile_blueprint(bp)
    pinned_result = compile_blueprint(bp, snapshot=pinned)
    assert isinstance(live, CompilerErrors)
    assert isinstance(pinned_result, CompilerErrors)
    live_msg = next(
        e.message for e in live.errors
        if e.code == CompileIssueCode.DEPRECATED_ACTION_ID.value and "give_aspirin" in e.message
    )
    pinned_msg = next(
        e.message for e in pinned_result.errors
        if e.code == CompileIssueCode.DEPRECATED_ACTION_ID.value and "give_aspirin" in e.message
    )
    assert "2099-01-01" in live_msg
    assert "2099-01-01" not in pinned_msg


def test_policy_rejection_codes_match_http_contract():
    """Enum values are the public wire contract — do not rename casually."""
    assert PolicyRejectionCode.SCOPE_DENIED.value == "SCOPE_DENIED"
    assert PolicyRejectionCode.AUTHOR_CANNOT_APPROVE.value == "AUTHOR_CANNOT_APPROVE"
    assert PolicyRejectionCode.DRY_RUN_HASH_MISMATCH.value == "DRY_RUN_HASH_MISMATCH"
    assert PolicyRejectionCode.CONTENT_HASH_DRIFT.value == "CONTENT_HASH_DRIFT"
    assert PolicyRejectionCode.DUPLICATE_APPROVER.value == "DUPLICATE_APPROVER"
