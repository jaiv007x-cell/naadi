"""Tests for the Nirikshak path through the scorer."""
from __future__ import annotations

import pytest

from services.pratibimb.app.case_gen.sampler import CaseSampler
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit
from services.pratibimb.app.eval.scorer import (
    Scorer,
    Turn,
    grade_case,
    grade_to_rubric,
    project_turns,
    score_session,
)
from services.pratibimb.app.physio.state import PhysioTrace as LegacyTrace
from shared.schemas.trace import PhysioTrace


BLUEPRINT = GradingBlueprint(
    case_id="TEST_CASE",
    case_version="1.0.0",
    rubric_version="0.3.0",
    hits=(
        RubricHit(id="dx.mi", axis=Axis.DIAGNOSTIC, matcher="diagnosis_stated",
                  params={"dx": "stemi"}, points=10),
        RubricHit(id="act.ecg", axis=Axis.ACTION, matcher="order_placed",
                  params={"order_id": "ecg_12l", "within_s": 600}, points=10),
        RubricHit(id="act.aspirin", axis=Axis.ACTION, matcher="drug_given",
                  params={"drug_id": "aspirin", "min_dose": 300}, points=10),
        RubricHit(id="comm.sbar", axis=Axis.COMMUNICATION, matcher="handoff_given",
                  params={"required_fields": ("situation", "recommendation")}, points=5),
        RubricHit(id="comm.escalate", axis=Axis.COMMUNICATION, matcher="escalation",
                  params={"target": "cath_lab"}, points=5),
        RubricHit(id="safe.no_nitrate", axis=Axis.SAFETY, matcher="drug_not_given",
                  params={"drug_id": "nitroglycerin"}, points=10,
                  required=True, fail_case_on_violation=True),
    ),
)


def _legacy_trace_with_aspirin() -> LegacyTrace:
    legacy = LegacyTrace()
    legacy.events = [
        {"kind": "dose", "drug": "aspirin", "amount_mg": 325, "route": "PO", "t": 180},
    ]
    return legacy


def _good_turns() -> list[Turn]:
    return [
        Turn(speaker="learner", text="looks like a STEMI", ts=100.0, action="dx:stemi"),
        Turn(speaker="learner", text="get an ECG", ts=120.0, action="order:ecg_12l"),
        Turn(speaker="learner", text="situation ... recommendation", ts=500.0,
             action="handoff:cardiology"),
        Turn(speaker="learner", text="activating", ts=300.0, action="escalate:cath_lab"),
        Turn(speaker="learner", text="how is the pain now?", ts=60.0, action=None),
    ]


# ── turn projection ──────────────────────────────────────────────────────────

def test_project_turns_maps_each_action_kind():
    trace = PhysioTrace()
    project_turns(trace, _good_turns())
    assert len(trace.orders("ecg_12l")) == 1
    assert len(trace.diagnoses("stemi")) == 1
    assert len(trace.escalations("cath_lab")) == 1
    assert len(trace.handoffs("cardiology")) == 1


def test_project_turns_ignores_plain_dialogue():
    trace = PhysioTrace()
    project_turns(trace, [Turn(speaker="learner", text="hello", ts=1.0, action=None)])
    assert trace.orders() == []
    assert trace.diagnoses() == []


def test_project_turns_extracts_sbar_fields_from_text():
    trace = PhysioTrace()
    project_turns(trace, [
        Turn(speaker="learner", text="Situation: chest pain. Recommendation: PCI.",
             ts=10.0, action="handoff:icu"),
    ])
    fields = trace.handoffs("icu")[0].fields
    assert "situation" in fields
    assert "recommendation" in fields
    assert "background" not in fields


# ── grade_case ───────────────────────────────────────────────────────────────

def test_grade_case_merges_physio_and_turn_evidence():
    grade, _ = grade_case(BLUEPRINT, _legacy_trace_with_aspirin(), _good_turns())
    assert grade.outcome("act.aspirin").matched is True   # from physio trace
    assert grade.outcome("act.ecg").matched is True       # from turns
    assert grade.outcome("dx.mi").matched is True
    assert grade.outcome("comm.sbar").matched is True
    assert grade.outcome("safe.no_nitrate").matched is True
    assert grade.critical_violations == ()
    assert grade.failed is False


def test_grade_case_uses_blueprint_versions():
    grade, _ = grade_case(BLUEPRINT, LegacyTrace(), [])
    assert grade.case_version == "1.0.0"
    assert grade.rubric_version == "0.3.0"


def test_grade_reports_blueprint_case_id_not_runtime_case_id():
    """The blueprint is authoritative for which case was graded."""
    grade, _ = grade_case(BLUEPRINT, LegacyTrace(), [], case_id="RUNTIME_ID")
    assert grade.case_id == "TEST_CASE"


def test_grade_case_returns_the_migration_audit():
    legacy = _legacy_trace_with_aspirin()
    legacy.events.append({"kind": "avatar_frame", "t": 5})
    _, report = grade_case(BLUEPRINT, legacy, _good_turns())
    assert report.dropped["avatar_frame"] == 1
    assert report.consumed["learner_turns"] == 4


# ── grade_to_rubric ──────────────────────────────────────────────────────────

def test_grade_to_rubric_maps_axes_into_dimensions():
    grade, _ = grade_case(BLUEPRINT, _legacy_trace_with_aspirin(), _good_turns())
    rubric = grade_to_rubric(grade)
    assert rubric.clinical_reasoning == pytest.approx(10.0)
    assert rubric.communication == pytest.approx(10.0)
    assert rubric.ethical_legal == pytest.approx(10.0)
    assert rubric.overall > 8.0


def test_grade_to_rubric_stays_within_bounds_on_empty_session():
    grade, _ = grade_case(BLUEPRINT, LegacyTrace(), [])
    rubric = grade_to_rubric(grade)
    for value in (rubric.clinical_reasoning, rubric.procedural_correctness,
                  rubric.communication, rubric.ethical_legal, rubric.stress_modulated):
        assert 0.0 <= value <= 10.0


def test_grade_to_rubric_notes_criticals_and_misses():
    legacy = LegacyTrace()
    legacy.events = [
        {"kind": "dose", "drug": "nitroglycerin", "amount_mg": 0.4, "route": "SL", "t": 100},
    ]
    grade, _ = grade_case(BLUEPRINT, legacy, [])
    notes = " ".join(grade_to_rubric(grade).notes)
    assert "critical violation: safe.no_nitrate" in notes
    assert "missed" in notes
    assert grade.evaluation_manifest_hash[:12] in notes


# ── score_session / Scorer integration ───────────────────────────────────────

def test_score_session_without_blueprint_has_no_case_grade():
    case = CaseSampler().sample()
    card = score_session(case, [], LegacyTrace())
    assert card.case_grade is None
    assert card.trace_migration is None
    assert card.total >= 0


def test_score_session_with_blueprint_attaches_case_grade():
    case = CaseSampler().sample()
    card = score_session(
        case, _good_turns(), _legacy_trace_with_aspirin(), blueprint=BLUEPRINT
    )
    assert card.case_grade is not None
    assert card.case_grade.outcome("act.ecg").matched is True
    assert len(card.case_grade.evaluation_manifest_hash) == 64
    assert card.trace_migration is not None
    assert card.trace_migration.consumed["dose"] == 1


@pytest.mark.asyncio
async def test_scorer_blueprint_path_returns_rubric():
    case = CaseSampler().sample()
    action_log = [
        {"action": "order_ecg_within_10min", "t": 120},
        {"action": "give_aspirin_325_chewed", "t": 180},
    ]
    transcript = [{"role": "student", "content": "Namaste", "lang": "mr"}]

    rubric = await Scorer().score(
        case, action_log, transcript, stress_delta=0.0,
        physio=_legacy_trace_with_aspirin(), blueprint=BLUEPRINT,
    )
    assert 0.0 <= rubric.overall <= 10.0
    assert any("nirikshak" in n for n in rubric.notes)


@pytest.mark.asyncio
async def test_scorer_without_blueprint_is_unchanged():
    case = CaseSampler().sample()
    rubric = await Scorer().score(
        case,
        [{"action": "order_ecg_within_10min", "t": 120}],
        [{"role": "student", "content": "Namaste", "lang": "mr"}],
        stress_delta=0.0,
    )
    assert rubric.overall > 0
    assert not any("nirikshak" in n for n in rubric.notes)


@pytest.mark.asyncio
async def test_scorer_applies_negative_stress_delta():
    case = CaseSampler().sample()
    rubric = await Scorer().score(
        case, [], [], stress_delta=-2.0,
        physio=_legacy_trace_with_aspirin(), blueprint=BLUEPRINT,
    )
    assert rubric.stress_modulated >= 0.0
