"""
The REST path must fail closed too.

`SessionManager.complete_session()` previously always produced a scorecard, so a
summative session could complete having never been rubric-graded. That is the
same silent-pass failure as `Session.finalize()` returning `None`, just reached
through a different entry point.
"""
from __future__ import annotations

import pytest

from services.pratibimb.app.eval.nirikshak import MissingGradingBlueprint
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit
from services.pratibimb.app.session.manager import SessionManager
from shared.schemas.session import AssessmentMode, CreateSessionRequest

BLUEPRINT = GradingBlueprint(
    case_id="REST_TEST", case_version="1.0.0", rubric_version="0.3.0",
    hits=(RubricHit(id="act.ecg", axis=Axis.ACTION, matcher="order_placed",
                    params={"order_id": "ecg"}, points=10),),
)


def _manager_with_session(mode: AssessmentMode):
    manager = SessionManager()
    resp = manager.create_session(
        CreateSessionRequest(learner_id="L1", assessment_mode=mode)
    )
    return manager, resp.session_id


# ── mode plumbing ────────────────────────────────────────────────────────────

def test_create_session_defaults_to_practice():
    manager = SessionManager()
    resp = manager.create_session(CreateSessionRequest(learner_id="L1"))
    assert manager._sessions[resp.session_id].assessment_mode is AssessmentMode.PRACTICE


def test_create_session_records_the_requested_mode():
    manager, sid = _manager_with_session(AssessmentMode.SUMMATIVE)
    assert manager._sessions[sid].assessment_mode is AssessmentMode.SUMMATIVE


# ── fail closed ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summative_completion_without_a_blueprint_raises():
    manager, sid = _manager_with_session(AssessmentMode.SUMMATIVE)
    with pytest.raises(MissingGradingBlueprint) as exc:
        await manager.complete_session(sid)
    assert exc.value.session_id == sid


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [AssessmentMode.PRACTICE, AssessmentMode.FORMATIVE])
async def test_low_stakes_completion_still_produces_a_report(mode):
    manager, sid = _manager_with_session(mode)
    report = await manager.complete_session(sid)
    assert report.session_id == sid
    assert report.rubric.overall >= 0


@pytest.mark.asyncio
async def test_summative_completion_succeeds_when_the_case_carries_a_blueprint():
    manager, sid = _manager_with_session(AssessmentMode.SUMMATIVE)
    state = manager._sessions[sid]
    object.__setattr__(state.case, "grading_blueprint", BLUEPRINT)

    report = await manager.complete_session(sid)
    assert report.session_id == sid
    assert any("nirikshak" in n for n in report.rubric.notes)


@pytest.mark.asyncio
async def test_failed_summative_completion_does_not_mark_the_session_complete():
    """A refused grade must leave the session open for preceptor follow-up."""
    from shared.schemas.session import SessionStatus

    manager, sid = _manager_with_session(AssessmentMode.SUMMATIVE)
    with pytest.raises(MissingGradingBlueprint):
        await manager.complete_session(sid)
    assert manager._sessions[sid].status is not SessionStatus.COMPLETED
