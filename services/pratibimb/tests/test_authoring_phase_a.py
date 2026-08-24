"""Phase A authoring harness: schema isolation, state machine, dry-run guards."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.eval.rubric import Axis, RubricHit, Severity
from services.pratibimb.authoring.constants import (
    AUTHORING_SCHEMA,
    SCOPE_APPROVE,
    SCOPE_SUBMIT,
    CaseWorkflowState,
    FixtureKind,
)
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.dry_run import run_dry_run
from services.pratibimb.authoring.errors import (
    AuthorCannotApproveError,
    DryRunFailedError,
    MissingFixturesError,
    PhaseNotImplementedError,
    ScopeDeniedError,
)
from services.pratibimb.authoring.migrations.runner import apply_pending_migrations
from services.pratibimb.authoring.models import (
    AuthoringAlembicVersion,
    AuthoringBase,
    GoldenFixtureRow,
)
from services.pratibimb.authoring.state_machine import (
    TransitionActor,
    execute_draft_to_in_review,
    validate_approver_distinct_from_author,
)
from services.pratibimb.authoring.store import CaseDraftStore
from services.pratibimb.ledger.models import Base as LedgerBase
from shared.schemas.trace import DrugAdminEvent, PhysioTrace


TENANT = "tenant-a"
AUTHOR = "author-1"
REVIEWER = "reviewer-2"


def _blueprint_json(*, case_id: str = "C1", case_version: str = "1.0.0") -> dict:
    return {
        "identity": {"case_id": case_id, "version": case_version},
        "grading_blueprint": {
            "case_id": case_id,
            "case_version": case_version,
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


def _pass_trace(case_id: str = "C1", case_version: str = "1.0.0") -> dict:
    trace = PhysioTrace(case_id=case_id, case_version=case_version)
    trace.record_drug(
        DrugAdminEvent(drug_id="aspirin", dose=325, dose_unit="mg", route="PO", t_s=60.0)
    )
    return trace.to_canonical()


def _miss_trace(case_id: str = "C1", case_version: str = "1.0.0") -> dict:
    return PhysioTrace(case_id=case_id, case_version=case_version).to_canonical()


def _fixture_row(
    draft_id: str,
    kind: FixtureKind,
    trace_json: dict,
    *,
    passed: bool,
) -> GoldenFixtureRow:
    return GoldenFixtureRow(
        id=f"{draft_id}-{kind.value}",
        draft_id=draft_id,
        fixture_kind=kind.value,
        trace_json=trace_json,
        expected_grade_json={"passed": passed},
        content_hash="abc123",
    )


@pytest.fixture
def authoring_session():
    # init_authoring_schema -> _sqlite_schema_shim attaches :memory: as `authoring`
    # so schema-qualified FKs (authoring.case_drafts) resolve under SQLite like Postgres.
    engine = create_engine("sqlite:///:memory:", future=True)
    init_authoring_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def store(authoring_session):
    return CaseDraftStore(authoring_session)


def _seed_draft_with_fixtures(store: CaseDraftStore, *, pass_fixtures: bool = True):
    draft = store.create_draft(
        tenant_id=TENANT,
        author_subject_id=AUTHOR,
        blueprint_json=_blueprint_json(),
        blueprint_version="1.0.0",
    )
    trace_pass = _pass_trace()
    trace_miss = _miss_trace()
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.PERFECT_PATH,
        trace_json=trace_pass,
        expected_grade_json={"passed": True},
    )
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.CRITICAL_MISS,
        trace_json=trace_miss,
        expected_grade_json={"passed": False},
    )
    if not pass_fixtures:
        # Break perfect-path expectation so dry-run fails.
        fixtures = store.list_fixtures(draft.id)
        for fx in fixtures:
            if fx.fixture_kind == FixtureKind.PERFECT_PATH.value:
                fx.expected_grade_json = {"passed": False}
    return draft


def test_schema_isolation_authoring_version_table_is_separate(authoring_session):
    engine = authoring_session.get_bind()
    apply_pending_migrations(engine)

    ledger_tables = set(LedgerBase.metadata.tables)
    authoring_tables = set(AuthoringBase.metadata.tables)
    assert not ledger_tables & authoring_tables

    assert AuthoringAlembicVersion.__table__.schema == AUTHORING_SCHEMA
    assert "alembic_version" not in LedgerBase.metadata.tables

    with engine.connect() as conn:
        authoring_tables_db = conn.execute(
            text(
                "SELECT name FROM authoring.sqlite_master "
                "WHERE type = 'table'"
            )
        ).fetchall()
    table_names = {row[0] for row in authoring_tables_db}
    assert "case_drafts" in table_names
    assert "alembic_version" in table_names

    versions = authoring_session.execute(
        text(f"SELECT version_num FROM {AUTHORING_SCHEMA}.alembic_version")
    ).fetchall()
    assert ("0001",) in versions


def test_state_machine_happy_path_draft_to_in_review(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    actor = TransitionActor(subject_id=AUTHOR, scopes=frozenset({SCOPE_SUBMIT}))

    transition = store.transition(
        draft.id,
        to_state=CaseWorkflowState.IN_REVIEW,
        actor=actor,
        reason="ready for review",
    )
    authoring_session.commit()

    refreshed = store.get_draft(draft.id)
    assert refreshed.current_state == CaseWorkflowState.IN_REVIEW.value
    assert transition.dry_run_result_hash is not None
    assert len(transition.dry_run_result_hash) == 64


def test_state_machine_rejects_author_as_approver(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    draft.current_state = CaseWorkflowState.IN_REVIEW.value
    authoring_session.flush()

    actor = TransitionActor(
        subject_id=AUTHOR,
        scopes=frozenset({SCOPE_APPROVE}),
    )
    with pytest.raises(AuthorCannotApproveError):
        store.transition(
            draft.id,
            to_state=CaseWorkflowState.APPROVED,
            actor=actor,
        )

    with pytest.raises(AuthorCannotApproveError):
        validate_approver_distinct_from_author(
            author_subject_id=AUTHOR,
            actor_subject_id=AUTHOR,
        )


def test_unimplemented_transition_raises_phase_d(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    draft.current_state = CaseWorkflowState.IN_REVIEW.value
    authoring_session.flush()

    actor = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE}))
    with pytest.raises(PhaseNotImplementedError, match="phase D"):
        store.transition(
            draft.id,
            to_state=CaseWorkflowState.PUBLISHED,
            actor=actor,
        )


def test_approve_distinct_reviewer_stamps_content_hash(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    submit_actor = TransitionActor(subject_id=AUTHOR, scopes=frozenset({SCOPE_SUBMIT}))
    store.transition(draft.id, to_state=CaseWorkflowState.IN_REVIEW, actor=submit_actor)
    authoring_session.flush()

    reviewer = TransitionActor(subject_id=REVIEWER, scopes=frozenset({SCOPE_APPROVE}))
    transition = store.transition(
        draft.id,
        to_state=CaseWorkflowState.APPROVED,
        actor=reviewer,
        reason="formative approve",
    )
    authoring_session.commit()
    refreshed = store.get_draft(draft.id)
    assert refreshed.current_state == CaseWorkflowState.APPROVED.value
    assert transition.content_hash_snapshot
    assert len(transition.content_hash_snapshot) == 64
    assert transition.actor_subject_id == REVIEWER
    submit = store.list_transitions(draft.id)[0]
    assert submit.to_state == CaseWorkflowState.IN_REVIEW.value
    assert transition.content_hash_snapshot == submit.content_hash_snapshot
    assert transition.validation_context_hash == submit.validation_context_hash
    assert submit.registry_snapshot_json is not None


def test_dry_run_pass_and_fail_cases(store):
    draft = store.create_draft(
        tenant_id=TENANT,
        author_subject_id=AUTHOR,
        blueprint_json=_blueprint_json(),
        blueprint_version="1.0.0",
    )
    fixtures = [
        _fixture_row(draft.id, FixtureKind.PERFECT_PATH, _pass_trace(), passed=True),
        _fixture_row(draft.id, FixtureKind.CRITICAL_MISS, _miss_trace(), passed=False),
    ]
    good = run_dry_run(draft.blueprint_json, fixtures)
    assert good.passed is True
    assert all(o.matched_expectation for o in good.outcomes)

    broken = [
        _fixture_row(draft.id, FixtureKind.PERFECT_PATH, _miss_trace(), passed=True),
        _fixture_row(draft.id, FixtureKind.CRITICAL_MISS, _miss_trace(), passed=False),
    ]
    bad = run_dry_run(draft.blueprint_json, broken)
    assert bad.passed is False


def test_transition_blocked_on_dry_run_failure(store):
    draft = _seed_draft_with_fixtures(store, pass_fixtures=False)
    actor = TransitionActor(subject_id=AUTHOR, scopes=frozenset({SCOPE_SUBMIT}))

    with pytest.raises(DryRunFailedError):
        store.transition(
            draft.id,
            to_state=CaseWorkflowState.IN_REVIEW,
            actor=actor,
        )


def test_missing_fixture_rejection(store):
    draft = store.create_draft(
        tenant_id=TENANT,
        author_subject_id=AUTHOR,
        blueprint_json=_blueprint_json(),
        blueprint_version="1.0.0",
    )
    store.upsert_fixture(
        draft.id,
        fixture_kind=FixtureKind.PERFECT_PATH,
        trace_json=_pass_trace(),
        expected_grade_json={"passed": True},
    )
    with pytest.raises(MissingFixturesError, match="critical_miss"):
        execute_draft_to_in_review(draft, store.list_fixtures(draft.id))


def test_submit_without_scope_is_denied(store):
    draft = _seed_draft_with_fixtures(store)
    actor = TransitionActor(subject_id=AUTHOR, scopes=frozenset())
    with pytest.raises(ScopeDeniedError):
        store.transition(
            draft.id,
            to_state=CaseWorkflowState.IN_REVIEW,
            actor=actor,
        )


def test_revert_in_review_to_draft(store, authoring_session):
    draft = _seed_draft_with_fixtures(store)
    submit_actor = TransitionActor(subject_id=AUTHOR, scopes=frozenset({SCOPE_SUBMIT}))
    store.transition(
        draft.id,
        to_state=CaseWorkflowState.IN_REVIEW,
        actor=submit_actor,
    )
    authoring_session.flush()

    transition = store.transition(
        draft.id,
        to_state=CaseWorkflowState.DRAFT,
        actor=submit_actor,
        reason="needs edits",
    )
    authoring_session.commit()

    refreshed = store.get_draft(draft.id)
    assert refreshed.current_state == CaseWorkflowState.DRAFT.value
    assert transition.dry_run_result_hash is None


def test_dry_run_result_hash_is_stable(store):
    draft = _seed_draft_with_fixtures(store)
    fixtures = store.list_fixtures(draft.id)
    first = run_dry_run(draft.blueprint_json, fixtures)
    second = run_dry_run(draft.blueprint_json, fixtures)
    assert first.result_hash == second.result_hash
