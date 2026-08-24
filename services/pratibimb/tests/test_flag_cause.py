from __future__ import annotations

import re
import pytest

from shared.schemas.case import CaseBlueprint
from shared.schemas.flag_cause import FlagCause, _CAUSE_PATTERN
from services.pratibimb.app.physio.state import PhysiologyEngine, SimClock


def _make_engine() -> PhysiologyEngine:
    case = CaseBlueprint(
        case_id="test",
        difficulty=0.5,
        demographics={
            "name": "Test", "age": 30, "sex": "M",
            "occupation": "test", "state": "Maharashtra", "city": "Mumbai",
            "native_language": "hi", "education_years": 10, "monthly_income_inr": 10000,
        },
        chief_complaint_verbatim={"hi": "test"},
        baseline_vitals={"hr": 80, "sbp": 120, "dbp": 80, "spo2": 98, "rr": 16, "temp_c": 37.0},
        hidden={
            "primary_diagnosis": "test", "icd10": "Z00",
            "onset_minutes_ago": 10,
            "symptoms_present": [], "symptoms_absent": [],
            "comorbidities": [], "current_meds": [],
            "allergies": [],
            "social_history": {}, "red_herrings": [],
        },
        expected_actions=[], critical_actions=[],
        time_pressure_seconds=900, family_present=False,
        resources_available=[],
    )
    return PhysiologyEngine(case, clock=SimClock(0.0))


def test_all_causes_are_registered_enum_values():
    for member in FlagCause:
        assert _CAUSE_PATTERN.match(member.value), (
            f"{member.name}={member.value!r} violates dotted namespace"
        )


def test_no_other_escape_hatch_exists():
    forbidden = {"OTHER", "UNKNOWN", "MISC", "GENERIC"}
    assert not (forbidden & {m.name for m in FlagCause})


def test_every_cause_has_registered_namespace():
    allowed_namespaces = {
        "drug", "physiology", "clinical", "procedure", "case", "learner",
    }
    for member in FlagCause:
        ns = FlagCause.namespace(member)
        assert ns in allowed_namespaces, f"{member} has rogue namespace {ns!r}"


def test_validate_registered_passes_on_clean_enum():
    FlagCause.validate_registered()


def test_set_flag_rejects_string_cause():
    engine = _make_engine()
    with pytest.raises(TypeError, match="FlagCause enum"):
        engine.set_flag("hypotension", cause="drug.allergy")  # type: ignore[arg-type]


def test_clear_flag_rejects_string_cause():
    engine = _make_engine()
    engine.set_flag("hypotension", cause=FlagCause.DRUG_ALLERGY)
    with pytest.raises(TypeError, match="FlagCause enum"):
        engine.clear_flag("hypotension", cause="physiology.homeostasis")  # type: ignore[arg-type]


def test_set_flag_records_cause_and_namespace():
    engine = _make_engine()
    engine.set_flag(
        "hypotension",
        cause=FlagCause.CLINICAL_ANAPHYLAXIS_ONSET,
        detail={"trigger_drug": "ceftriaxone"},
    )
    entry = engine.flag_history[-1]
    assert entry["cause"] == "clinical.anaphylaxis_onset"
    assert entry["namespace"] == "clinical"
    assert entry["detail"] == {"trigger_drug": "ceftriaxone"}
    assert entry["set"] is True


def test_clear_flag_default_cause_is_homeostasis():
    engine = _make_engine()
    engine.set_flag("hypoxia", cause=FlagCause.CLINICAL_HYPOXIA)
    engine.clear_flag("hypoxia")
    entry = engine.flag_history[-1]
    assert entry["cause"] == "physiology.homeostasis"


def test_prefix_filter_works_on_history():
    engine = _make_engine()
    engine.set_flag("a", cause=FlagCause.DRUG_ADMIN)
    engine.set_flag("b", cause=FlagCause.CLINICAL_HYPOXIA)
    engine.set_flag("c", cause=FlagCause.DRUG_ALLERGY)
    drug_events = [e for e in engine.flag_history if e["cause"].startswith("drug.")]
    assert len(drug_events) == 2


def test_physio_trace_set_flag_requires_enum():
    from services.pratibimb.app.physio.state import PhysioTrace

    trace = PhysioTrace()
    with pytest.raises(TypeError, match="FlagCause enum"):
        trace.set_flag("hypotension", 1.0, cause="drug.allergy")  # type: ignore[arg-type]
