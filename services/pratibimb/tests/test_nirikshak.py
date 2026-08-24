"""Tests for Nirikshak — typed trace, positive-only semantics, manifest hashing."""
from __future__ import annotations

import pytest

from services.pratibimb.app.eval.nirikshak import (
    CASE_START,
    CaseGrade,
    GraderContext,
    Nirikshak,
)
from services.pratibimb.app.physio.version_probe import get_version as get_physio_version
from services.pratibimb.app.eval.rubric import (
    NIRIKSHAK_VERSION,
    Axis,
    GradingBlueprint,
    RubricHit,
    Severity,
    letter_for,
)
from shared.schemas.trace import (
    DiagnosisEvent,
    DrugAdminEvent,
    EscalationEvent,
    FlagEvent,
    HandoffEvent,
    OrderEvent,
    PhysioTrace,
    RecognitionEvent,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _trace() -> PhysioTrace:
    return PhysioTrace(case_id="C1", case_version="1.0.0")


def _bp(*hits: RubricHit, **kwargs) -> GradingBlueprint:
    kwargs.setdefault("case_id", "C1")
    kwargs.setdefault("case_version", "1.0.0")
    kwargs.setdefault("rubric_version", "0.3.0")
    return GradingBlueprint(hits=tuple(hits), **kwargs)


def _grade(blueprint: GradingBlueprint, trace: PhysioTrace) -> CaseGrade:
    return Nirikshak(blueprint, trace).grade()


def _outcome(grade: CaseGrade, hit_id: str):
    o = grade.outcome(hit_id)
    assert o is not None, f"no outcome for {hit_id}"
    return o


# ── grade envelope ───────────────────────────────────────────────────────────

def test_empty_blueprint_scores_full_marks():
    """Axes with no hits normalize to 1.0, so an empty rubric cannot fail anyone."""
    grade = _grade(_bp(), _trace())
    assert grade.overall == pytest.approx(1.0)
    assert grade.letter == "A"
    assert grade.failed is False
    assert grade.critical_violations == ()


def test_grade_carries_all_versions():
    grade = _grade(_bp(), _trace())
    assert grade.grader_version == NIRIKSHAK_VERSION
    assert grade.physio_version == get_physio_version()
    assert grade.case_version == "1.0.0"
    assert grade.rubric_version == "0.3.0"


def test_to_dict_round_trip():
    hit = RubricHit(id="h1", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "aspirin"}, points=5)
    grade = _grade(_bp(hit), _trace())
    d = grade.to_dict()
    assert d["case_id"] == "C1"
    assert d["outcomes"][0]["hit_id"] == "h1"
    assert d["outcomes"][0]["matched"] is False
    assert len(d["evaluation_manifest_hash"]) == 64


def test_passed_is_inverse_of_failed():
    grade = _grade(_bp(), _trace())
    assert grade.passed is not grade.failed


def test_letter_bands():
    assert letter_for(0.95) == "A"
    assert letter_for(0.85) == "B"
    assert letter_for(0.75) == "C"
    assert letter_for(0.65) == "D"
    assert letter_for(0.10) == "F"


# ── positive-only semantics ──────────────────────────────────────────────────

def test_miss_awards_zero_never_negative():
    hit = RubricHit(id="h1", axis=Axis.SAFETY, matcher="drug_given",
                    params={"drug_id": "adrenaline"}, points=10,
                    severity=Severity.CRITICAL)
    grade = _grade(_bp(hit), _trace())
    assert _outcome(grade, "h1").points_awarded == 0.0
    assert all(a.earned >= 0.0 for a in grade.axis_scores)


def test_critical_violation_requires_both_flags():
    """`required` alone records a miss; failing the case needs fail_case_on_violation."""
    required_only = RubricHit(id="req", axis=Axis.ACTION, matcher="drug_given",
                              params={"drug_id": "adrenaline"}, points=5, required=True)
    grade = _grade(_bp(required_only), _trace())
    assert grade.critical_violations == ()

    both = RubricHit(id="crit", axis=Axis.ACTION, matcher="drug_given",
                     params={"drug_id": "adrenaline"}, points=5,
                     required=True, fail_case_on_violation=True)
    grade = _grade(_bp(both), _trace())
    assert grade.critical_violations == ("crit",)
    assert grade.failed is True


def test_critical_violation_fails_even_at_high_score():
    good = RubricHit(id="good", axis=Axis.ACTION, matcher="drug_given",
                     params={"drug_id": "adrenaline"}, points=100)
    crit = RubricHit(id="crit", axis=Axis.SAFETY, matcher="drug_not_given",
                     params={"drug_id": "ceftriaxone"}, points=1,
                     required=True, fail_case_on_violation=True)
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=60))
    t.record_drug(DrugAdminEvent("ceftriaxone", 1000, "mg", "IV", t_s=80))
    grade = _grade(_bp(good, crit), t)
    assert _outcome(grade, "good").matched is True
    assert grade.critical_violations == ("crit",)
    assert grade.failed is True


# ── drug_given ───────────────────────────────────────────────────────────────

def test_drug_given_within_dose_and_route():
    hit = RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "adrenaline", "route": "IM",
                            "min_dose": 0.3, "max_dose": 0.5, "within_s": 300},
                    points=10)
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=120))
    grade = _grade(_bp(hit), t)
    assert _outcome(grade, "epi").matched is True
    assert _outcome(grade, "epi").points_awarded == 10


def test_drug_given_rejects_wrong_route():
    hit = RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "adrenaline", "route": "IM"}, points=10)
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IV", t_s=120))
    assert _outcome(_grade(_bp(hit), t), "epi").matched is False


def test_drug_given_rejects_dose_outside_window():
    hit = RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "adrenaline", "min_dose": 0.3, "max_dose": 0.5},
                    points=10)
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.1, "mg", "IM", t_s=60))
    t.record_drug(DrugAdminEvent("adrenaline", 5.0, "mg", "IM", t_s=90))
    assert _outcome(_grade(_bp(hit), t), "epi").matched is False


def test_drug_given_rejects_late_administration():
    hit = RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "adrenaline", "within_s": 180}, points=10)
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=400))
    assert _outcome(_grade(_bp(hit), t), "epi").matched is False


def test_drug_given_after_flag_anchor():
    hit = RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "adrenaline", "after_flag": "shock",
                            "within_s": 120},
                    points=10)
    t = _trace()
    t.record_flag(FlagEvent("shock", True, t_s=100))
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=150))
    assert _outcome(_grade(_bp(hit), t), "epi").matched is True


def test_drug_given_before_anchor_does_not_count():
    hit = RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "adrenaline", "after_flag": "shock"}, points=10)
    t = _trace()
    t.record_flag(FlagEvent("shock", True, t_s=200))
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=50))
    assert _outcome(_grade(_bp(hit), t), "epi").matched is False


def test_unresolvable_anchor_fails_hit():
    """A never-set anchor flag must not silently make the constraint vacuous."""
    hit = RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "adrenaline", "after_flag": "never_fired"},
                    points=10)
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=60))
    outcome = _outcome(_grade(_bp(hit), t), "epi")
    assert outcome.matched is False
    assert outcome.evidence["reason"] == "anchor_flag_never_set"


def test_case_start_anchor_is_zero():
    ctx = GraderContext(_trace())
    assert ctx.reference_time(None) == 0.0
    assert ctx.reference_time("") == 0.0
    assert ctx.reference_time(CASE_START) == 0.0


# ── drug_not_given (safe behaviour demonstrated) ─────────────────────────────

def test_drug_not_given_awards_points_when_withheld():
    hit = RubricHit(id="safe", axis=Axis.SAFETY, matcher="drug_not_given",
                    params={"drug_id": "metoprolol"}, points=6)
    grade = _grade(_bp(hit), _trace())
    assert _outcome(grade, "safe").matched is True
    assert _outcome(grade, "safe").points_awarded == 6


def test_drug_not_given_awards_zero_when_administered():
    hit = RubricHit(id="safe", axis=Axis.SAFETY, matcher="drug_not_given",
                    params={"drug_id": "metoprolol"}, points=6)
    t = _trace()
    t.record_drug(DrugAdminEvent("metoprolol", 5, "mg", "IV", t_s=100))
    outcome = _outcome(_grade(_bp(hit), t), "safe")
    assert outcome.matched is False
    assert outcome.points_awarded == 0.0
    assert len(outcome.evidence["violations"]) == 1


def test_drug_not_given_is_route_scoped():
    """Withholding IV adrenaline still earns credit when IM was given correctly."""
    hit = RubricHit(id="safe", axis=Axis.SAFETY, matcher="drug_not_given",
                    params={"drug_id": "adrenaline", "route": "IV"}, points=10)
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=60))
    assert _outcome(_grade(_bp(hit), t), "safe").matched is True


def test_drug_not_given_scoped_to_flag_span():
    hit = RubricHit(id="safe", axis=Axis.SAFETY, matcher="drug_not_given",
                    params={"drug_id": "metoprolol", "while_flag": "anaphylaxis"},
                    points=6)
    t = _trace()
    t.record_flag(FlagEvent("anaphylaxis", True, t_s=100))
    t.record_flag(FlagEvent("anaphylaxis", False, t_s=200))
    t.record_drug(DrugAdminEvent("metoprolol", 5, "mg", "IV", t_s=400))
    # Given well after the flag cleared, so safe behaviour during the span holds.
    assert _outcome(_grade(_bp(hit), t), "safe").matched is True


def test_drug_not_given_violated_inside_flag_span():
    hit = RubricHit(id="safe", axis=Axis.SAFETY, matcher="drug_not_given",
                    params={"drug_id": "metoprolol", "while_flag": "anaphylaxis"},
                    points=6)
    t = _trace()
    t.record_flag(FlagEvent("anaphylaxis", True, t_s=100))
    t.record_flag(FlagEvent("anaphylaxis", False, t_s=300))
    t.record_drug(DrugAdminEvent("metoprolol", 5, "mg", "IV", t_s=150))
    assert _outcome(_grade(_bp(hit), t), "safe").matched is False


# ── order_placed ─────────────────────────────────────────────────────────────

def test_order_placed_with_param_constraint():
    hit = RubricHit(id="o2", axis=Axis.ACTION, matcher="order_placed",
                    params={"order_id": "oxygen", "params": {"lpm_min": 6},
                            "within_s": 240},
                    points=6)
    t = _trace()
    t.record_order(OrderEvent("oxygen", t_s=100, params={"lpm": 8}))
    assert _outcome(_grade(_bp(hit), t), "o2").matched is True


def test_order_placed_rejects_param_below_minimum():
    hit = RubricHit(id="o2", axis=Axis.ACTION, matcher="order_placed",
                    params={"order_id": "oxygen", "params": {"lpm_min": 6}}, points=6)
    t = _trace()
    t.record_order(OrderEvent("oxygen", t_s=100, params={"lpm": 2}))
    assert _outcome(_grade(_bp(hit), t), "o2").matched is False


def test_order_placed_equality_constraint():
    hit = RubricHit(id="fluids", axis=Axis.ACTION, matcher="order_placed",
                    params={"order_id": "iv_fluids", "params": {"fluid": "NS"}}, points=5)
    t = _trace()
    t.record_order(OrderEvent("iv_fluids", t_s=100, params={"fluid": "RL"}))
    assert _outcome(_grade(_bp(hit), t), "fluids").matched is False


def test_order_placed_missing():
    hit = RubricHit(id="fluids", axis=Axis.ACTION, matcher="order_placed",
                    params={"order_id": "iv_fluids"}, points=5)
    assert _outcome(_grade(_bp(hit), _trace()), "fluids").matched is False


# ── diagnosis / recognitions ─────────────────────────────────────────────────

def test_diagnosis_stated_via_alias():
    hit = RubricHit(id="dx", axis=Axis.DIAGNOSTIC, matcher="diagnosis_stated",
                    params={"dx": "anaphylaxis",
                            "aliases": ("anaphylactic shock",), "within_s": 180},
                    points=10)
    t = _trace()
    t.record_diagnosis(DiagnosisEvent("anaphylactic shock", 0.9, t_s=100))
    assert _outcome(_grade(_bp(hit), t), "dx").matched is True


def test_diagnosis_stated_exact_match():
    hit = RubricHit(id="dx", axis=Axis.DIAGNOSTIC, matcher="diagnosis_stated",
                    params={"dx": "anaphylaxis"}, points=10)
    t = _trace()
    t.record_diagnosis(DiagnosisEvent("Anaphylaxis", 0.95, t_s=60))
    assert _outcome(_grade(_bp(hit), t), "dx").matched is True


def test_diagnosis_stated_no_match():
    hit = RubricHit(id="dx", axis=Axis.DIAGNOSTIC, matcher="diagnosis_stated",
                    params={"dx": "anaphylaxis"}, points=10)
    t = _trace()
    t.record_diagnosis(DiagnosisEvent("asthma exacerbation", 0.9, t_s=60))
    assert _outcome(_grade(_bp(hit), t), "dx").matched is False


def test_diagnosis_below_confidence_threshold_is_a_miss():
    hit = RubricHit(id="dx", axis=Axis.DIAGNOSTIC, matcher="diagnosis_stated",
                    params={"dx": "anaphylaxis", "min_confidence": 0.8}, points=10)
    t = _trace()
    t.record_diagnosis(DiagnosisEvent("anaphylaxis", 0.3, t_s=100))
    assert _outcome(_grade(_bp(hit), t), "dx").matched is False


@pytest.mark.parametrize(
    "matcher,kind",
    [
        ("vital_recognized", "vital"),
        ("finding_recognized", "finding"),
        ("symptom_elicited", "symptom"),
        ("physical_sign_identified", "physical_sign"),
    ],
)
def test_recognition_matchers(matcher, kind):
    hit = RubricHit(id="r", axis=Axis.DIAGNOSTIC, matcher=matcher,
                    params={"token": "tok"}, points=3)
    t = _trace()
    t.record_recognition(RecognitionEvent(kind, "tok", t_s=50))
    assert _outcome(_grade(_bp(hit), t), "r").matched is True


def test_recognition_wrong_kind_does_not_match():
    hit = RubricHit(id="r", axis=Axis.DIAGNOSTIC, matcher="vital_recognized",
                    params={"token": "stridor"}, points=3)
    t = _trace()
    t.record_recognition(RecognitionEvent("physical_sign", "stridor", t_s=50))
    assert _outcome(_grade(_bp(hit), t), "r").matched is False


# ── escalation / handoff ─────────────────────────────────────────────────────

def test_escalation_within_window():
    hit = RubricHit(id="esc", axis=Axis.COMMUNICATION, matcher="escalation",
                    params={"target": "physician_on_call", "within_s": 300}, points=4)
    t = _trace()
    t.record_escalation(EscalationEvent("physician_on_call", t_s=200))
    assert _outcome(_grade(_bp(hit), t), "esc").matched is True


def test_escalation_outside_window():
    hit = RubricHit(id="esc", axis=Axis.COMMUNICATION, matcher="escalation",
                    params={"target": "physician_on_call", "within_s": 100}, points=4)
    t = _trace()
    t.record_escalation(EscalationEvent("physician_on_call", t_s=500))
    assert _outcome(_grade(_bp(hit), t), "esc").matched is False


def test_handoff_requires_all_sbar_fields():
    hit = RubricHit(id="sbar", axis=Axis.COMMUNICATION, matcher="handoff_given",
                    params={"required_fields": ("situation", "background",
                                                "assessment", "recommendation")},
                    points=5)
    t = _trace()
    t.record_handoff(HandoffEvent(
        frozenset({"situation", "background", "assessment", "recommendation"}),
        "icu", t_s=400,
    ))
    assert _outcome(_grade(_bp(hit), t), "sbar").matched is True


def test_handoff_missing_field_is_a_miss():
    hit = RubricHit(id="sbar", axis=Axis.COMMUNICATION, matcher="handoff_given",
                    params={"required_fields": ("situation", "background",
                                                "assessment", "recommendation")},
                    points=5)
    t = _trace()
    t.record_handoff(HandoffEvent(frozenset({"situation", "background"}), "icu", t_s=400))
    assert _outcome(_grade(_bp(hit), t), "sbar").matched is False


# ── flag matchers ────────────────────────────────────────────────────────────

def test_flag_present_and_cleared():
    present = RubricHit(id="f1", axis=Axis.SAFETY, matcher="flag_present",
                        params={"flag": "documented"}, points=4)
    cleared = RubricHit(id="f2", axis=Axis.SAFETY, matcher="flag_cleared",
                        params={"flag": "shock"}, points=4)
    t = _trace()
    t.record_flag(FlagEvent("documented", True, t_s=100))
    t.record_flag(FlagEvent("shock", True, t_s=50))
    t.record_flag(FlagEvent("shock", False, t_s=150))
    grade = _grade(_bp(present, cleared), t)
    assert _outcome(grade, "f1").matched is True
    assert _outcome(grade, "f2").matched is True


def test_flag_present_outside_window_is_a_miss():
    hit = RubricHit(id="f", axis=Axis.TIMING, matcher="flag_present",
                    params={"flag": "documented", "within_s": 60}, points=4)
    t = _trace()
    t.record_flag(FlagEvent("documented", True, t_s=300))
    assert _outcome(_grade(_bp(hit), t), "f").matched is False


# ── sequence ─────────────────────────────────────────────────────────────────

def _sequence_blueprint(max_gap_s: float | None = 300) -> GradingBlueprint:
    return _bp(
        RubricHit(id="dx", axis=Axis.DIAGNOSTIC, matcher="diagnosis_stated",
                  params={"dx": "anaphylaxis"}, points=5),
        RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                  params={"drug_id": "adrenaline"}, points=5),
        RubricHit(id="fluids", axis=Axis.ACTION, matcher="order_placed",
                  params={"order_id": "iv_fluids"}, points=5),
        RubricHit(id="seq", axis=Axis.TIMING, matcher="sequence",
                  params={"steps": ["dx", "epi", "fluids"], "max_gap_s": max_gap_s},
                  points=6),
    )


def test_sequence_in_correct_order():
    t = _trace()
    t.record_diagnosis(DiagnosisEvent("anaphylaxis", 1.0, t_s=60))
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=120))
    t.record_order(OrderEvent("iv_fluids", t_s=240))
    grade = _grade(_sequence_blueprint(), t)
    outcome = _outcome(grade, "seq")
    assert outcome.matched is True
    assert [c["id"] for c in outcome.evidence["chain"]] == ["dx", "epi", "fluids"]


def test_sequence_out_of_order_fails():
    t = _trace()
    t.record_diagnosis(DiagnosisEvent("anaphylaxis", 1.0, t_s=300))
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=120))
    t.record_order(OrderEvent("iv_fluids", t_s=240))
    outcome = _outcome(_grade(_sequence_blueprint(), t), "seq")
    assert outcome.matched is False
    assert outcome.evidence["reason"] == "out_of_order"


def test_sequence_gap_exceeded_fails():
    t = _trace()
    t.record_diagnosis(DiagnosisEvent("anaphylaxis", 1.0, t_s=10))
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=20))
    t.record_order(OrderEvent("iv_fluids", t_s=5000))
    outcome = _outcome(_grade(_sequence_blueprint(max_gap_s=300), t), "seq")
    assert outcome.matched is False
    assert outcome.evidence["reason"] == "gap_exceeded"


def test_sequence_missing_step_reports_failure_point():
    t = _trace()
    t.record_diagnosis(DiagnosisEvent("anaphylaxis", 1.0, t_s=60))
    outcome = _outcome(_grade(_sequence_blueprint(), t), "seq")
    assert outcome.matched is False
    assert outcome.evidence["failed_at"] == "epi"


def test_sequence_unknown_step_id():
    bp = _bp(RubricHit(id="seq", axis=Axis.TIMING, matcher="sequence",
                       params={"steps": ["nope"]}, points=6))
    outcome = _outcome(_grade(bp, _trace()), "seq")
    assert outcome.matched is False
    assert "unknown step" in outcome.evidence["error"]


# ── action_within ────────────────────────────────────────────────────────────

def test_action_within_tightens_inner_window():
    bp = _bp(
        RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                  params={"drug_id": "adrenaline"}, points=15),
        RubricHit(id="fast", axis=Axis.TIMING, matcher="action_within",
                  params={"inner_hit": "epi", "within_s": 180}, points=8),
    )
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=400))
    grade = _grade(bp, t)
    assert _outcome(grade, "epi").matched is True     # given at all
    assert _outcome(grade, "fast").matched is False   # but not fast enough


def test_action_within_matches_when_fast():
    bp = _bp(
        RubricHit(id="epi", axis=Axis.ACTION, matcher="drug_given",
                  params={"drug_id": "adrenaline"}, points=15),
        RubricHit(id="fast", axis=Axis.TIMING, matcher="action_within",
                  params={"inner_hit": "epi", "within_s": 180}, points=8),
    )
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=100))
    assert _outcome(_grade(bp, t), "fast").matched is True


def test_action_within_unknown_inner_hit():
    bp = _bp(RubricHit(id="fast", axis=Axis.TIMING, matcher="action_within",
                       params={"inner_hit": "ghost", "within_s": 60}, points=8))
    outcome = _outcome(_grade(bp, _trace()), "fast")
    assert outcome.matched is False
    assert "unknown inner_hit" in outcome.evidence["error"]


# ── unknown matcher ──────────────────────────────────────────────────────────

def test_unknown_matcher_is_a_miss_not_a_crash():
    bp = _bp(RubricHit(id="h", axis=Axis.ACTION, matcher="teleport",
                       params={}, points=5))
    outcome = _outcome(_grade(bp, _trace()), "h")
    assert outcome.matched is False
    assert "unknown matcher" in outcome.evidence["error"]


# ── axis rollup and weighting ────────────────────────────────────────────────

def test_axis_normalization():
    bp = _bp(
        RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                  params={"drug_id": "adrenaline"}, points=6),
        RubricHit(id="b", axis=Axis.ACTION, matcher="drug_given",
                  params={"drug_id": "saline"}, points=4),
    )
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=60))
    grade = _grade(bp, t)
    action = grade.axis_score(Axis.ACTION)
    assert action.earned == 6
    assert action.max_points == 10
    assert action.normalized == pytest.approx(0.6)


def test_empty_axis_normalizes_to_one():
    bp = _bp(RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                       params={"drug_id": "adrenaline"}, points=5))
    grade = _grade(bp, _trace())
    assert grade.axis_score(Axis.COMMUNICATION).normalized == 1.0


def test_weights_drive_overall():
    bp = _bp(
        RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                  params={"drug_id": "adrenaline"}, points=10),
        weights={Axis.ACTION: 1.0},
        pass_threshold=0.5,
    )
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=60))
    assert _grade(bp, t).overall == pytest.approx(1.0)
    assert _grade(bp, _trace()).overall == pytest.approx(0.0)


# ── evaluation manifest hash ─────────────────────────────────────────────────

def test_manifest_hash_is_deterministic():
    bp = _bp(RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                       params={"drug_id": "adrenaline"}, points=5))
    t = _trace()
    t.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=60))
    assert _grade(bp, t).evaluation_manifest_hash == _grade(bp, t).evaluation_manifest_hash


def test_manifest_hash_changes_with_trace():
    bp = _bp(RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                       params={"drug_id": "adrenaline"}, points=5))
    empty = _trace()
    dosed = _trace()
    dosed.record_drug(DrugAdminEvent("adrenaline", 0.4, "mg", "IM", t_s=60))
    assert _grade(bp, empty).evaluation_manifest_hash != _grade(bp, dosed).evaluation_manifest_hash


def test_manifest_hash_changes_with_rubric_content():
    """Editing points must change the hash even when rubric_version is unchanged."""
    t = _trace()
    a = _bp(RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                      params={"drug_id": "adrenaline"}, points=5))
    b = _bp(RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                      params={"drug_id": "adrenaline"}, points=9))
    assert _grade(a, t).evaluation_manifest_hash != _grade(b, t).evaluation_manifest_hash


def test_manifest_includes_versions():
    manifest = Nirikshak(_bp(), _trace()).evaluation_manifest()
    assert manifest["grader_version"] == NIRIKSHAK_VERSION
    assert manifest["physio_version"] == get_physio_version()
    assert "trace" in manifest
    assert "rubric" in manifest


# ── blueprint helpers ────────────────────────────────────────────────────────

def test_max_points_counts_every_hit():
    bp = _bp(
        RubricHit(id="a", axis=Axis.SAFETY, matcher="drug_not_given",
                  params={"drug_id": "x"}, points=6),
        RubricHit(id="b", axis=Axis.SAFETY, matcher="drug_not_given",
                  params={"drug_id": "y"}, points=4),
    )
    assert bp.max_points(Axis.SAFETY) == 10


def test_hit_lookup():
    hit = RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "x"}, points=1)
    bp = _bp(hit)
    assert bp.hit("a") is hit
    assert bp.hit("missing") is None


# ── the shipped anaphylaxis blueprint ────────────────────────────────────────

def test_anaphylaxis_blueprint_shape():
    from services.pratibimb.app.cases.rubrics.anaphylaxis import ANAPHYLAXIS_BLUEPRINT as bp

    assert bp.case_id == "ANAPHYLAXIS_OPD_001"
    assert bp.display_name
    assert len(bp.hits) >= 20
    for axis in Axis:
        assert bp.hits_by_axis(axis), f"{axis.value} axis has no hits"
    assert sum(bp.weights.values()) == pytest.approx(1.0)


def test_anaphylaxis_blueprint_has_no_negative_points():
    from services.pratibimb.app.cases.rubrics.anaphylaxis import ANAPHYLAXIS_BLUEPRINT as bp

    assert all(h.points > 0 for h in bp.hits)


def test_anaphylaxis_sequence_steps_resolve():
    from services.pratibimb.app.cases.rubrics.anaphylaxis import ANAPHYLAXIS_BLUEPRINT as bp

    for hit in bp.hits:
        if hit.matcher == "sequence":
            for step in hit.params["steps"]:
                assert bp.hit(step) is not None, f"{hit.id} references missing step {step}"
        if hit.matcher == "action_within":
            assert bp.hit(hit.params["inner_hit"]) is not None


def test_anaphylaxis_empty_session_fails_on_criticals():
    from services.pratibimb.app.cases.rubrics.anaphylaxis import ANAPHYLAXIS_BLUEPRINT as bp

    grade = _grade(bp, _trace())
    assert grade.failed is True
    assert "act.adrenaline_im" in grade.critical_violations
    assert "dx.anaphylaxis" in grade.critical_violations
    # Withholding hits still earn credit for an untreated patient.
    assert _outcome(grade, "safe.beta_blocker_withheld").matched is True


def test_anaphylaxis_competent_run_passes():
    from services.pratibimb.app.cases.rubrics.anaphylaxis import ANAPHYLAXIS_BLUEPRINT as bp

    t = PhysioTrace(case_id=bp.case_id, case_version=bp.case_version)
    t.record_recognition(RecognitionEvent("vital", "sbp_low", t_s=40))
    t.record_recognition(RecognitionEvent("vital", "spo2_low", t_s=45))
    t.record_recognition(RecognitionEvent("physical_sign", "stridor", t_s=50))
    t.record_recognition(RecognitionEvent("finding", "urticaria", t_s=55))
    t.record_recognition(RecognitionEvent("symptom", "allergy_history", t_s=30))
    t.record_diagnosis(DiagnosisEvent("anaphylaxis", 0.95, t_s=60))
    t.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=90))
    t.record_order(OrderEvent("oxygen", t_s=120, params={"lpm": 10}))
    t.record_order(OrderEvent("iv_fluids", t_s=180, params={"fluid": "NS", "bolus_ml": 1000}))
    t.record_order(OrderEvent("position_supine_legs_raised", t_s=100))
    t.record_drug(DrugAdminEvent("hydrocortisone", 200, "mg", "IV", t_s=300))
    t.record_drug(DrugAdminEvent("pheniramine", 25, "mg", "IV", t_s=320))
    t.record_order(OrderEvent("prepare_intubation", t_s=200))
    t.record_order(OrderEvent("observe_biphasic", t_s=600))
    t.record_flag(FlagEvent("adrenaline_repeat_considered", True, t_s=400))
    t.record_flag(FlagEvent("allergy_documented", True, t_s=500))
    t.record_flag(FlagEvent("consent_explained_family", True, t_s=350))
    t.record_escalation(EscalationEvent("physician_on_call", t_s=150))
    t.record_handoff(HandoffEvent(
        frozenset({"situation", "background", "assessment", "recommendation"}),
        "icu", t_s=650,
    ))

    grade = _grade(bp, t)
    assert grade.critical_violations == ()
    assert grade.failed is False
    assert grade.overall > 0.9
    assert grade.letter in ("A", "B")


def test_anaphylaxis_iv_bolus_fails_critical():
    """IV adrenaline bolus in a conscious patient is a critical safety violation."""
    from services.pratibimb.app.cases.rubrics.anaphylaxis import ANAPHYLAXIS_BLUEPRINT as bp

    t = PhysioTrace(case_id=bp.case_id, case_version=bp.case_version)
    t.record_recognition(RecognitionEvent("vital", "sbp_low", t_s=40))
    t.record_recognition(RecognitionEvent("vital", "spo2_low", t_s=45))
    t.record_recognition(RecognitionEvent("physical_sign", "stridor", t_s=50))
    t.record_recognition(RecognitionEvent("finding", "urticaria", t_s=55))
    t.record_recognition(RecognitionEvent("symptom", "allergy_history", t_s=30))
    t.record_diagnosis(DiagnosisEvent("anaphylaxis", 0.95, t_s=60))
    t.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=90))
    t.record_drug(DrugAdminEvent("adrenaline", 1.0, "mg", "IV", t_s=95))
    t.record_order(OrderEvent("oxygen", t_s=120, params={"lpm": 10}))
    t.record_order(OrderEvent("iv_fluids", t_s=180, params={"fluid": "NS", "bolus_ml": 1000}))
    t.record_escalation(EscalationEvent("physician_on_call", t_s=150))

    grade = _grade(bp, t)
    assert "safe.iv_adrenaline_withheld" in grade.critical_violations
    assert grade.failed is True
