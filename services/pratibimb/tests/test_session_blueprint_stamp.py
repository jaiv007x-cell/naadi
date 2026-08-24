"""Phase E1 chunk 3: session-create blueprint_content_hash snapshot wiring."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit, Severity
from services.pratibimb.app.session.manager import SessionManager
from services.pratibimb.authoring.constants import HARNESS_VERSION, PolicyRejectionCode
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.errors import (
    BlueprintHashDriftError,
    PublishedCaseNotFoundError,
)
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.db import seed_trusted_physio_version
from services.pratibimb.ledger.models import Base, SessionLedgerRow
from services.pratibimb.ledger.writer import append_graded_session
from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.app.eval.scorer import Turn
from services.pratibimb.app.physio.state import PhysioTrace as LegacyPhysioTrace
from shared.schemas.flag_cause import FlagCause
from shared.schemas.session import AssessmentMode, CreateSessionRequest
from shared.schemas.trace import DiagnosisEvent

TENANT = "tenant-a"
CASE_ID = "anaphylaxis_01"
CASE_VERSION = "v2.0"
HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture
def authoring_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_authoring_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def ledger_db(monkeypatch):
    monkeypatch.delenv("LEDGER_DISABLED", raising=False)
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    seed_trusted_physio_version(engine, approved_by="test")
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        yield session


def _seed_published(
    authoring_session,
    *,
    content_hash: str = HASH_A,
    retired: bool = False,
) -> PublishedCaseVersionRow:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=TENANT,
        author_subject_id="author-1",
        blueprint_json={"identity": {"case_id": CASE_ID, "version": CASE_VERSION}},
        blueprint_version=CASE_VERSION,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        current_state="PUBLISHED",
        harness_version=HARNESS_VERSION,
    )
    authoring_session.add(draft)
    authoring_session.flush()
    now = datetime.now(timezone.utc)
    row = PublishedCaseVersionRow(
        id=str(uuid.uuid4()),
        draft_id=draft.id,
        tenant_id=TENANT,
        case_id=CASE_ID,
        version=CASE_VERSION,
        assessment_mode="formative",
        content_hash=content_hash,
        harness_version=HARNESS_VERSION,
        published_at=now,
        published_by="publisher-5",
        retired_at=now if retired else None,
        retired_by="retirer-6" if retired else None,
        retired_reason_code="policy_change" if retired else None,
    )
    authoring_session.add(row)
    authoring_session.flush()
    return row


def _published_create_req() -> CreateSessionRequest:
    return CreateSessionRequest(
        learner_id="L1",
        assessment_mode=AssessmentMode.PRACTICE,
        tenant_id=TENANT,
        case_id=CASE_ID,
        case_version=CASE_VERSION,
    )


def _grade_bundle():
    blueprint = GradingBlueprint(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        rubric_version="0.3.0",
        hits=(
            RubricHit(
                id="dx.anaphylaxis",
                axis=Axis.DIAGNOSTIC,
                matcher="diagnosis_stated",
                params={"dx": "anaphylaxis"},
                points=10.0,
            ),
            RubricHit(
                id="act.epinephrine",
                axis=Axis.ACTION,
                matcher="drug_given",
                params={"drug_id": "epinephrine"},
                points=15.0,
                required=True,
                fail_case_on_violation=True,
                severity=Severity.CRITICAL,
            ),
        ),
    )
    legacy = LegacyPhysioTrace()
    legacy.set_flag(
        "hypotension",
        60.0,
        cause=FlagCause.DRUG_PD_EFFECT,
        detail={"drug": "morphine"},
    )
    legacy.clear_flag("hypotension", 180.0, cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS)
    legacy.add_event(80.0, "diagnosis", {"dx": "anaphylaxis", "confidence": 0.9})
    typed, _ = legacy.to_typed_trace(case_id=CASE_ID, case_version=CASE_VERSION)
    typed.record_diagnosis(DiagnosisEvent(dx="anaphylaxis", confidence=0.9, t_s=80.0))
    grade = Nirikshak(blueprint, typed).grade()
    turns = [Turn(speaker="learner", text="anaphylaxis", ts=80.0, action="dx:anaphylaxis")]
    return blueprint, legacy, grade, turns


def test_happy_create_stamps_current_published_hash(authoring_session):
    _seed_published(authoring_session, content_hash=HASH_A)
    manager = SessionManager(authoring_session=authoring_session)
    resp = manager.create_session(_published_create_req())
    state = manager._sessions[resp.session_id]
    assert state.blueprint_content_hash == HASH_A
    assert state.tenant_id == TENANT
    assert state.case_version == CASE_VERSION
    assert state.case.case_id == CASE_ID


def test_create_against_missing_version_raises_no_session(authoring_session):
    manager = SessionManager(authoring_session=authoring_session)
    with pytest.raises(PublishedCaseNotFoundError) as exc:
        manager.create_session(_published_create_req())
    assert exc.value.code is PolicyRejectionCode.PUBLISHED_CASE_NOT_FOUND
    assert manager._sessions == {}


def test_create_then_hash_change_then_finalize_drifts(
    authoring_session, ledger_db
):
    """Create-time snapshot is authoritative — mutate published hash after create."""
    pub = _seed_published(authoring_session, content_hash=HASH_A)
    manager = SessionManager(authoring_session=authoring_session)
    resp = manager.create_session(_published_create_req())
    state = manager._sessions[resp.session_id]
    assert state.blueprint_content_hash == HASH_A

    # Simulate post-create published-byte change for the same version triple.
    pub.content_hash = HASH_B
    authoring_session.flush()

    blueprint, legacy, grade, turns = _grade_bundle()
    with pytest.raises(BlueprintHashDriftError) as exc:
        append_graded_session(
            ledger_db,
            session_id=resp.session_id,
            learner_pseudo_id=state.learner_id,
            cohort_id="cohortA",
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            grade=grade,
            blueprint=blueprint,
            legacy_trace=legacy,
            turns=turns,
            blueprint_content_hash=state.blueprint_content_hash,
            tenant_id=state.tenant_id,
            authoring_session=authoring_session,
        )
    assert exc.value.stamped_hash == HASH_A
    assert exc.value.published_hash == HASH_B
    assert ledger_db.get(SessionLedgerRow, resp.session_id) is None


def test_create_then_retire_then_finalize_succeeds(authoring_session, ledger_db):
    pub = _seed_published(authoring_session, content_hash=HASH_A)
    manager = SessionManager(authoring_session=authoring_session)
    resp = manager.create_session(_published_create_req())
    state = manager._sessions[resp.session_id]

    pub.retired_at = datetime.now(timezone.utc)
    pub.retired_by = "retirer-6"
    pub.retired_reason_code = "policy_change"
    authoring_session.flush()

    blueprint, legacy, grade, turns = _grade_bundle()
    append_graded_session(
        ledger_db,
        session_id=resp.session_id,
        learner_pseudo_id=state.learner_id,
        cohort_id="cohortA",
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash=state.blueprint_content_hash,
        tenant_id=state.tenant_id,
        authoring_session=authoring_session,
    )
    row = ledger_db.get(SessionLedgerRow, resp.session_id)
    assert row is not None
    assert row.blueprint_content_hash == HASH_A


def test_legacy_create_leaves_hash_null(authoring_session):
    manager = SessionManager(authoring_session=authoring_session)
    resp = manager.create_session(CreateSessionRequest(learner_id="L1"))
    state = manager._sessions[resp.session_id]
    assert state.blueprint_content_hash is None
    assert state.tenant_id is None
    assert state.case_version is None


def test_partial_published_pick_is_programmer_error(authoring_session):
    manager = SessionManager(authoring_session=authoring_session)
    with pytest.raises(ValueError, match="together"):
        manager.create_session(
            CreateSessionRequest(
                learner_id="L1",
                tenant_id=TENANT,
                case_id=CASE_ID,
                # case_version missing
            )
        )
    assert manager._sessions == {}
