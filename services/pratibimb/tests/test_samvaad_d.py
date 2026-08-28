"""SAMVAAD.d — 8-row / 11-test frozen acceptance matrix."""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.pratibimb.samvaad.bias_remediation import (
    ERROR_KIND_ILLEGAL_TRANSITION,
    BiasRemediationError,
)
from services.pratibimb.samvaad.eval_harness import SamvaadEvalHarness, SamvaadRubricHit
from services.pratibimb.samvaad.evidence_class import EvidenceClass
from services.pratibimb.samvaad.formative import clear_formative_store
from services.pratibimb.samvaad.formative_projector import formative_ledger_rows
from services.pratibimb.samvaad.illegal_transition_detector import (
    detect_illegal_transition,
)
from services.pratibimb.samvaad.ledger_projector import (
    clear_summative_ledger,
    summative_eligible_rows,
    summative_ledger_rows,
)
from services.pratibimb.samvaad.matcher_spec import REQUIRED_STEPS
from services.pratibimb.samvaad.runtime import (
    ERROR_KIND_IMPLICIT_CUE_FORBIDDEN,
    ERROR_KIND_INVALID_MATCHER_CONFIG,
    SamvaadMatcherError,
    evaluate_empathy_and,
)
from services.pratibimb.samvaad.schema import validate_reference_rubric
from services.pratibimb.samvaad.time_on_task import (
    clear_time_on_task,
    record_session_time,
    rolling_time_on_task_seconds,
    time_on_task_records,
)
from services.pratibimb.samvaad.verifier_emit import clear_verifier_emits
from services.pratibimb.samvaad.verify_service import verify_samvaad

pytestmark = pytest.mark.samvaad_d_acceptance

_REPO = Path(__file__).resolve().parents[3]
_PRATIBIMB = _REPO / "services" / "pratibimb"


@pytest.fixture(autouse=True)
def _clean_d_stores():
    clear_formative_store()
    clear_summative_ledger()
    clear_time_on_task()
    clear_verifier_emits()
    yield
    clear_formative_store()
    clear_summative_ledger()
    clear_time_on_task()
    clear_verifier_emits()


def _empathy_steps_at(timestamp_ms: int) -> dict[str, int]:
    return {step: timestamp_ms for step in REQUIRED_STEPS}


def test_samvaad_d_1a_empathy_window_inclusive_upper_bound():
    steps = {step: True for step in REQUIRED_STEPS}
    matched, evidence = evaluate_empathy_and(
        steps,
        interruptions_after_ack=0,
        cue_onset_ms=10_000,
        window_ms=5_000,
        step_timestamps_ms=_empathy_steps_at(15_000),
    )
    assert matched is True
    assert evidence["window_ms"] == 5_000


def test_samvaad_d_1b_empathy_window_outside_upper_bound():
    steps = {step: True for step in REQUIRED_STEPS}
    matched, _ = evaluate_empathy_and(
        steps,
        interruptions_after_ack=0,
        cue_onset_ms=10_000,
        window_ms=5_000,
        step_timestamps_ms=_empathy_steps_at(15_001),
    )
    assert matched is False


def test_samvaad_d_2_implicit_cue_default_rejection():
    steps = {step: True for step in REQUIRED_STEPS}
    with pytest.raises(SamvaadMatcherError) as exc:
        evaluate_empathy_and(
            steps,
            interruptions_after_ack=0,
            cue_explicit=False,
        )
    assert exc.value.error_kind == ERROR_KIND_IMPLICIT_CUE_FORBIDDEN
    matched, evidence = evaluate_empathy_and(
        steps,
        interruptions_after_ack=0,
        cue_explicit=True,
    )
    assert matched is True
    assert evidence["allow_implicit_cue"] is False


def test_samvaad_d_3_min_occurrences_per_turn_and_zero_parse_rejection():
    harness = SamvaadEvalHarness(
        {"debrief_utterances": ["used box breathing", "used box breathing again"]}
    )
    hit_n = SamvaadRubricHit(
        params={
            "techniques_referenced": ["box_breathing"],
            "min_occurrences_per_turn": 2,
        },
        matcher={"kind": "debrief_self_report_structured"},
    )
    passed, _ = harness.evaluate_kind("debrief_self_report_structured", hit_n)
    hit_n_plus_one = SamvaadRubricHit(
        params={
            "techniques_referenced": ["box_breathing"],
            "min_occurrences_per_turn": 3,
        },
        matcher={"kind": "debrief_self_report_structured"},
    )
    failed, _ = harness.evaluate_kind("debrief_self_report_structured", hit_n_plus_one)
    assert passed is True
    assert failed is False
    with pytest.raises(SamvaadMatcherError) as exc:
        validate_reference_rubric(
            {
                "domain": "I",
                "competency": "stress.self_regulation.technique_deployed",
                "matcher": {"kind": "debrief_self_report_structured"},
                "params": {"min_occurrences_per_turn": 0},
                "required": False,
                "points": 1,
                "fail_case_on_violation": False,
            }
        )
    assert exc.value.error_kind == ERROR_KIND_INVALID_MATCHER_CONFIG


def test_samvaad_d_4a_formative_capture_022_with_provenance():
    output = verify_samvaad(
        {
            "assessment_kind": "formative",
            "payload": {
                "evidence_class": EvidenceClass.PATIENT_REPORTED.value,
                "source_context": {"instrument": "teach_back_survey"},
                "submitted_by": "patient-opaque-17",
                "session_anchor": "session-022",
                "matcher_parameters": {"window_ms": 5_000, "matched": True},
            },
        }
    )
    rows = formative_ledger_rows()
    assert output["status"] == "ok"
    assert len(rows) == 1
    assert rows[0]["assessment_kind"] == "formative"
    assert rows[0]["evidence_class"] == EvidenceClass.PATIENT_REPORTED.value
    assert rows[0]["source_context_json"]
    assert rows[0]["submitted_by"]
    assert rows[0]["session_anchor"]
    assert rows[0]["matcher_parameters_json"]
    assert rows[0]["captured_at_utc"]
    assert summative_ledger_rows() == []
    migration_022 = (
        _PRATIBIMB / "ledger/migrations/022_samvaad_formative_evidence.sql"
    ).read_text(encoding="utf-8")
    migration_021 = (
        _PRATIBIMB / "ledger/migrations/021_samvaad_summative_evidence.sql"
    ).read_text(encoding="utf-8")
    assert "CHECK (assessment_kind = 'formative')" in migration_022
    assert "CHECK (assessment_kind = 'summative')" in migration_021
    assert "patient_reported" not in migration_021


def test_samvaad_d_4b_summative_read_uses_positive_filter():
    import services.pratibimb.samvaad.ledger_projector as projector

    projector._LEDGER.extend(  # noqa: SLF001 - deliberate corruption regression
        [
            {"assessment_kind": "summative", "evidence_class": "machine_sim"},
            {"assessment_kind": "summative", "evidence_class": "patient_reported"},
            {"assessment_kind": "formative", "evidence_class": "machine_sim"},
        ]
    )
    rows = summative_eligible_rows()
    assert rows == [{"assessment_kind": "summative", "evidence_class": "machine_sim"}]


def test_samvaad_d_5_runtime_illegal_transition_reuses_error_kind():
    with pytest.raises(BiasRemediationError) as exc:
        detect_illegal_transition("retraining", "removed")
    assert exc.value.error_kind == ERROR_KIND_ILLEGAL_TRANSITION


def test_samvaad_d_6a_time_on_task_session_duration():
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    record = record_session_time(
        "session-1",
        started_at_utc=start,
        ended_at_utc=start + timedelta(minutes=17),
    )
    assert record.duration_seconds == 17 * 60
    assert time_on_task_records() == [record]


def test_samvaad_d_6b_time_on_task_request_time_30_day_roll():
    day_1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    for day in range(30):
        start = day_1 + timedelta(days=day)
        record_session_time(
            f"session-{day + 1}",
            started_at_utc=start,
            ended_at_utc=start + timedelta(minutes=1),
        )
    day_30_read = day_1 + timedelta(days=29, minutes=1)
    assert rolling_time_on_task_seconds(as_of_utc=day_30_read) == 30 * 60
    day_31_start = day_1 + timedelta(days=30)
    record_session_time(
        "session-31",
        started_at_utc=day_31_start,
        ended_at_utc=day_31_start + timedelta(minutes=1),
    )
    assert rolling_time_on_task_seconds(
        as_of_utc=day_31_start + timedelta(minutes=1)
    ) == 30 * 60


def test_samvaad_d_7_regression_runs_c_evidence_class_fixtures():
    nodes = [
        "services/pratibimb/tests/test_samvaad_c.py::test_samvaad_c_6_formative_accepts_patient_reported",
        "services/pratibimb/tests/test_samvaad_c.py::test_samvaad_c_7_summative_rejects_patient_reported_422_counter",
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *nodes, "-q", "--tb=no"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(r"\b2 passed\b", proc.stdout)


def test_samvaad_d_8_compose_literals_and_single_verifier_call_site():
    expected_counts = {
        "samvaad_acceptance": ("services/pratibimb/tests/test_samvaad_a.py", 22),
        "samvaad_b_acceptance": ("services/pratibimb/tests/test_samvaad_b.py", 14),
        "samvaad_c_acceptance": ("services/pratibimb/tests/test_samvaad_c.py", 18),
    }
    for marker, (test_file, expected) in expected_counts.items():
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                test_file,
                "-m",
                marker,
                "-q",
                "--tb=no",
            ],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert re.search(rf"\b{expected} passed\b", proc.stdout), (
            f"{marker} expected literal {expected}: {proc.stdout}"
        )
    assert 22 + 14 + 18 == 54

    sites: list[str] = []
    for path in _PRATIBIMB.rglob("*.py"):
        if path.name == "verifier_emit.py" or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "emit_samvaad_verifier_audit"
            ) or (
                isinstance(func, ast.Attribute)
                and func.attr == "emit_samvaad_verifier_audit"
            ):
                sites.append(str(path.relative_to(_REPO)).replace("\\", "/"))
                break
    assert sites == ["services/pratibimb/samvaad/verify_service.py"]
