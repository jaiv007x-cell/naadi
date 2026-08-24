"""Phase E1 chunk 2: blueprint hash resolve + fail-closed finalize drift check."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit, Severity
from services.pratibimb.app.eval.scorer import Turn
from services.pratibimb.app.physio.state import PhysioTrace as LegacyPhysioTrace
from services.pratibimb.authoring.constants import HARNESS_VERSION, PolicyRejectionCode
from services.pratibimb.authoring.db import init_authoring_schema
from services.pratibimb.authoring.errors import (
    BlueprintHashDriftError,
    PublishedCaseNotFoundError,
)
from services.pratibimb.authoring.models import CaseDraftRow, PublishedCaseVersionRow
from services.pratibimb.ledger.blueprint_hash import (
    resolve_published_content_hash,
    verify_blueprint_hash_at_finalize,
)
from services.pratibimb.ledger.db import seed_trusted_physio_version
from services.pratibimb.ledger.models import Base, SessionLedgerRow
from services.pratibimb.ledger.writer import append_graded_session
from shared.schemas.flag_cause import FlagCause
from shared.schemas.trace import DiagnosisEvent

TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"
CASE_ID = "anaphylaxis_01"
CASE_VERSION = "v2.0"
PUBLISHED_HASH = "a" * 64
OTHER_HASH = "b" * 64


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
    tenant_id: str = TENANT,
    case_id: str = CASE_ID,
    version: str = CASE_VERSION,
    content_hash: str = PUBLISHED_HASH,
    retired: bool = False,
) -> PublishedCaseVersionRow:
    draft = CaseDraftRow(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        author_subject_id="author-1",
        blueprint_json={"identity": {"case_id": case_id, "version": version}},
        blueprint_version=version,
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
        tenant_id=tenant_id,
        case_id=case_id,
        version=version,
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


def _legacy_trace() -> LegacyPhysioTrace:
    trace = LegacyPhysioTrace()
    trace.set_flag(
        "hypotension",
        60.0,
        cause=FlagCause.DRUG_PD_EFFECT,
        detail={"drug": "morphine"},
    )
    trace.clear_flag("hypotension", 180.0, cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS)
    trace.add_event(80.0, "diagnosis", {"dx": "anaphylaxis", "confidence": 0.9})
    return trace


def _blueprint() -> GradingBlueprint:
    return GradingBlueprint(
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


def _grade_and_turns(legacy: LegacyPhysioTrace, blueprint: GradingBlueprint):
    typed, _ = legacy.to_typed_trace(
        case_id=blueprint.case_id,
        case_version=blueprint.case_version,
    )
    typed.record_diagnosis(DiagnosisEvent(dx="anaphylaxis", confidence=0.9, t_s=80.0))
    grade = Nirikshak(blueprint, typed).grade()
    turns = [Turn(speaker="learner", text="anaphylaxis", ts=80.0, action="dx:anaphylaxis")]
    return grade, turns


def test_resolve_returns_published_hash(authoring_session):
    _seed_published(authoring_session)
    assert (
        resolve_published_content_hash(
            authoring_session,
            tenant_id=TENANT,
            case_id=CASE_ID,
            case_version=CASE_VERSION,
        )
        == PUBLISHED_HASH
    )


def test_happy_path_stamped_matches_published_append_succeeds(authoring_session, ledger_db):
    _seed_published(authoring_session)
    legacy = _legacy_trace()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)

    replay = append_graded_session(
        ledger_db,
        session_id="sess-ok",
        learner_pseudo_id="L1",
        cohort_id="cohortA",
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash=PUBLISHED_HASH,
        tenant_id=TENANT,
        authoring_session=authoring_session,
    )
    row = ledger_db.get(SessionLedgerRow, "sess-ok")
    assert row is not None
    assert row.blueprint_content_hash == PUBLISHED_HASH
    assert row.replay_hash == replay


def test_drift_refuses_append_no_ledger_row(authoring_session, ledger_db):
    _seed_published(authoring_session)
    legacy = _legacy_trace()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)

    with pytest.raises(BlueprintHashDriftError) as exc:
        append_graded_session(
            ledger_db,
            session_id="sess-drift",
            learner_pseudo_id="L1",
            cohort_id="cohortA",
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            grade=grade,
            blueprint=blueprint,
            legacy_trace=legacy,
            turns=turns,
            blueprint_content_hash=OTHER_HASH,
            tenant_id=TENANT,
            authoring_session=authoring_session,
        )

    assert exc.value.code is PolicyRejectionCode.BLUEPRINT_HASH_DRIFT
    assert exc.value.errors[0].path == "session.sess-drift.blueprint_content_hash"
    assert PUBLISHED_HASH[:12] in str(exc.value)
    assert OTHER_HASH[:12] in str(exc.value)
    assert exc.value.stamped_hash == OTHER_HASH
    assert exc.value.published_hash == PUBLISHED_HASH
    assert ledger_db.get(SessionLedgerRow, "sess-drift") is None
    assert len(ledger_db.execute(select(SessionLedgerRow)).scalars().all()) == 0


def test_retired_but_matching_hash_append_succeeds(authoring_session, ledger_db):
    _seed_published(authoring_session, retired=True)
    legacy = _legacy_trace()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)

    append_graded_session(
        ledger_db,
        session_id="sess-retired",
        learner_pseudo_id="L1",
        cohort_id="cohortA",
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash=PUBLISHED_HASH,
        tenant_id=TENANT,
        authoring_session=authoring_session,
    )
    assert ledger_db.get(SessionLedgerRow, "sess-retired") is not None


def test_not_found_distinct_from_drift(authoring_session, ledger_db):
    legacy = _legacy_trace()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)

    with pytest.raises(PublishedCaseNotFoundError) as exc:
        append_graded_session(
            ledger_db,
            session_id="sess-missing",
            learner_pseudo_id="L1",
            cohort_id="cohortA",
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            grade=grade,
            blueprint=blueprint,
            legacy_trace=legacy,
            turns=turns,
            blueprint_content_hash=PUBLISHED_HASH,
            tenant_id=TENANT,
            authoring_session=authoring_session,
        )

    assert exc.value.code is PolicyRejectionCode.PUBLISHED_CASE_NOT_FOUND
    assert ledger_db.get(SessionLedgerRow, "sess-missing") is None


def test_legacy_null_skips_drift_check(authoring_session, ledger_db):
    # Even with no published row, null stamp skips resolve entirely.
    legacy = _legacy_trace()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)

    append_graded_session(
        ledger_db,
        session_id="sess-legacy",
        learner_pseudo_id="L1",
        cohort_id="cohortA",
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy,
        turns=turns,
        blueprint_content_hash=None,
    )
    row = ledger_db.get(SessionLedgerRow, "sess-legacy")
    assert row is not None
    assert row.blueprint_content_hash is None


def test_tenant_isolation_same_case_version_other_tenant(authoring_session, ledger_db):
    _seed_published(authoring_session, tenant_id=OTHER_TENANT)
    legacy = _legacy_trace()
    blueprint = _blueprint()
    grade, turns = _grade_and_turns(legacy, blueprint)

    with pytest.raises(PublishedCaseNotFoundError):
        append_graded_session(
            ledger_db,
            session_id="sess-x-tenant",
            learner_pseudo_id="L1",
            cohort_id="cohortA",
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            grade=grade,
            blueprint=blueprint,
            legacy_trace=legacy,
            turns=turns,
            blueprint_content_hash=PUBLISHED_HASH,
            tenant_id=TENANT,
            authoring_session=authoring_session,
        )
    assert ledger_db.get(SessionLedgerRow, "sess-x-tenant") is None


def test_verify_skipped_when_stamped_null(authoring_session):
    _seed_published(authoring_session)
    # Must not raise even if we would otherwise resolve.
    verify_blueprint_hash_at_finalize(
        authoring_session,
        session_id="sess-skip",
        tenant_id=TENANT,
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        stamped_hash=None,
    )
