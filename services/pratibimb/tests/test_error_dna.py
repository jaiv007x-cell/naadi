"""Tests for Error-DNA (Performance Pattern Vector) computation."""

from __future__ import annotations

import pytest

from shared.schemas.trace import (
    PhysioTrace,
    DrugAdminEvent,
    FlagEvent,
    OrderEvent,
    EscalationEvent,
    RecognitionEvent,
    DiagnosisEvent,
)
from shared.schemas.evidence import EvidenceKind, EVIDENCE_KIND_PRIOR_WEIGHT
from services.pratibimb.app.eval.error_dna import (
    ErrorDNA,
    compute_error_dna,
    _lis_length,
    _CRISIS_FLAGS,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_trace(**kwargs) -> PhysioTrace:
    t = PhysioTrace(case_id="test", case_version="1.0.0")
    for d in kwargs.get("drugs", []):
        t.record_drug(d)
    for f in kwargs.get("flags", []):
        t.record_flag(f)
    for o in kwargs.get("orders", []):
        t.record_order(o)
    for e in kwargs.get("escalations", []):
        t.record_escalation(e)
    for r in kwargs.get("recognitions", []):
        t.record_recognition(r)
    for dx in kwargs.get("diagnoses", []):
        t.record_diagnosis(dx)
    return t


# ── ErrorDNA dataclass ────────────────────────────────────────────────────────

def test_error_dna_to_dict():
    dna = ErrorDNA(0.9, 0.8, 0.7, 0.6, 0.5, 0.4)
    d = dna.to_dict()
    assert d["recognition_delay"] == 0.9
    assert d["communication_score"] == 0.4
    assert len(d) == 6


def test_error_dna_as_vector():
    dna = ErrorDNA(0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    assert dna.as_vector() == (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)


def test_error_dna_mean():
    dna = ErrorDNA(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    assert dna.mean == 1.0
    dna2 = ErrorDNA(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert dna2.mean == 0.0


# ── recognition_delay ─────────────────────────────────────────────────────────

def test_recognition_delay_no_crisis():
    """No crisis flags → perfect recognition (nothing to recognize)."""
    trace = _make_trace()
    dna = compute_error_dna(trace)
    assert dna.recognition_delay == 1.0


def test_recognition_delay_fast_recognition():
    """Crisis at t=100, recognition at t=110 → near-perfect."""
    trace = _make_trace(
        flags=[FlagEvent("cardiogenic_shock", True, 100.0)],
        recognitions=[RecognitionEvent("vital", "sbp_low", 110.0)],
    )
    dna = compute_error_dna(trace, time_pressure_s=300.0)
    assert dna.recognition_delay > 0.9


def test_recognition_delay_no_recognition():
    """Crisis but no recognition events → 0.0."""
    trace = _make_trace(
        flags=[FlagEvent("cardiogenic_shock", True, 100.0)],
    )
    dna = compute_error_dna(trace)
    assert dna.recognition_delay == 0.0


# ── action_sequencing ─────────────────────────────────────────────────────────

def test_sequencing_perfect_order():
    """Actions in expected order → 1.0."""
    trace = _make_trace(
        orders=[OrderEvent("ecg", 60.0), OrderEvent("troponin", 120.0)],
        drugs=[DrugAdminEvent("aspirin", 325.0, "mg", "PO", 180.0)],
    )
    dna = compute_error_dna(
        trace,
        expected_sequence=["ecg", "troponin", "aspirin"],
    )
    assert dna.action_sequencing == 1.0


def test_sequencing_reversed_order():
    """Actions reversed → less than 1.0."""
    trace = _make_trace(
        drugs=[DrugAdminEvent("aspirin", 325.0, "mg", "PO", 60.0)],
        orders=[OrderEvent("troponin", 120.0), OrderEvent("ecg", 180.0)],
    )
    dna = compute_error_dna(
        trace,
        expected_sequence=["ecg", "troponin", "aspirin"],
    )
    assert dna.action_sequencing < 1.0


def test_sequencing_empty():
    """No expected sequence → 1.0."""
    trace = _make_trace()
    dna = compute_error_dna(trace, expected_sequence=[])
    assert dna.action_sequencing == 1.0


# ── dose_accuracy ─────────────────────────────────────────────────────────────

def test_dose_accuracy_exact():
    trace = _make_trace(
        drugs=[DrugAdminEvent("aspirin", 325.0, "mg", "PO", 60.0)],
    )
    dna = compute_error_dna(trace, expected_doses={"aspirin": 325.0})
    assert dna.dose_accuracy == 1.0


def test_dose_accuracy_wrong():
    trace = _make_trace(
        drugs=[DrugAdminEvent("aspirin", 162.5, "mg", "PO", 60.0)],
    )
    dna = compute_error_dna(trace, expected_doses={"aspirin": 325.0})
    assert dna.dose_accuracy == pytest.approx(0.5, abs=0.01)


def test_dose_accuracy_no_expected():
    trace = _make_trace()
    dna = compute_error_dna(trace)
    assert dna.dose_accuracy == 0.5


# ── escalation_timing ─────────────────────────────────────────────────────────

def test_escalation_fast():
    trace = _make_trace(
        flags=[FlagEvent("cardiogenic_shock", True, 100.0)],
        escalations=[EscalationEvent("code_blue", 110.0)],
    )
    dna = compute_error_dna(trace, time_pressure_s=300.0)
    assert dna.escalation_timing > 0.9


def test_escalation_never():
    trace = _make_trace(
        flags=[FlagEvent("cardiogenic_shock", True, 100.0)],
    )
    dna = compute_error_dna(trace)
    assert dna.escalation_timing == 0.0


def test_escalation_no_crisis():
    trace = _make_trace()
    dna = compute_error_dna(trace)
    assert dna.escalation_timing == 1.0


# ── recovery_velocity ─────────────────────────────────────────────────────────

def test_recovery_fast():
    """Crisis flag set and cleared within 30s → near-perfect."""
    trace = _make_trace(
        flags=[
            FlagEvent("cardiogenic_shock", True, 100.0),
            FlagEvent("cardiogenic_shock", False, 130.0),
        ],
    )
    dna = compute_error_dna(trace)
    assert dna.recovery_velocity > 0.9


def test_recovery_slow():
    """Crisis flag never cleared (open span to end) → low score."""
    trace = _make_trace(
        flags=[FlagEvent("cardiogenic_shock", True, 100.0)],
    )
    trace._t_end = 800.0
    dna = compute_error_dna(trace)
    assert dna.recovery_velocity < 0.1


def test_recovery_no_crisis():
    trace = _make_trace()
    dna = compute_error_dna(trace)
    assert dna.recovery_velocity == 1.0


# ── communication_score ───────────────────────────────────────────────────────

def test_communication_default():
    trace = _make_trace()
    dna = compute_error_dna(trace)
    assert dna.communication_score == 0.5


def test_communication_override():
    trace = _make_trace()
    dna = compute_error_dna(trace, communication_score=0.9)
    assert dna.communication_score == 0.9


# ── integration: CaseGrade carries error_dna ──────────────────────────────────

def test_grade_includes_error_dna():
    """Nirikshak.grade() should populate error_dna on CaseGrade."""
    from services.pratibimb.app.eval.nirikshak import Nirikshak
    from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit

    bp = GradingBlueprint(
        case_id="test", case_version="1.0", rubric_version="1.0",
        hits=(
            RubricHit(id="ecg", axis=Axis.ACTION, matcher="order_placed",
                      points=5.0, params={"order_id": "ecg_12l"}),
        ),
    )
    trace = PhysioTrace(case_id="test", case_version="1.0")
    trace.record_order(OrderEvent("ecg_12l", 45.0))

    grader = Nirikshak(bp, trace)
    grade = grader.grade()
    assert grade.error_dna is not None
    assert "recognition_delay" in grade.error_dna
    assert len(grade.error_dna) == 6


# ── EvidenceKind.ERROR_DNA ────────────────────────────────────────────────────

def test_evidence_kind_error_dna_exists():
    assert EvidenceKind.ERROR_DNA.value == "error_dna"
    assert EvidenceKind.ERROR_DNA in EVIDENCE_KIND_PRIOR_WEIGHT


def test_evidence_kind_error_dna_weight():
    w = EVIDENCE_KIND_PRIOR_WEIGHT[EvidenceKind.ERROR_DNA]
    assert 0.8 <= w <= 0.95


# ── LIS helper ────────────────────────────────────────────────────────────────

def test_lis_empty():
    assert _lis_length([]) == 0


def test_lis_sorted():
    assert _lis_length([1.0, 2.0, 3.0, 4.0]) == 4


def test_lis_reversed():
    assert _lis_length([4.0, 3.0, 2.0, 1.0]) == 1
