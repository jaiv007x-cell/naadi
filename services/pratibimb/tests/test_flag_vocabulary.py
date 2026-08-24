"""Tests for flag cause/reason vocabulary enforcement."""

from __future__ import annotations

from shared.schemas.flag_causes import (
    CAUSE_REGISTRY,
    CLEAR_REGISTRY,
    FlagCause,
    FlagClearReason,
)


def test_all_causes_have_descriptions():
    for c in FlagCause:
        assert c in CAUSE_REGISTRY, f"{c} missing description"


def test_all_clear_reasons_have_descriptions():
    for r in FlagClearReason:
        assert r in CLEAR_REGISTRY, f"{r} missing description"


def test_engine_allergy_flags_carry_cause():
    """Allergy path should tag flags with ALLERGIC_REACTION cause."""
    from shared.schemas.case import CaseBlueprint
    from services.pratibimb.app.physio.state import PhysiologyEngine, SimClock

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
            "allergies": ["aspirin"],
            "social_history": {}, "red_herrings": [],
        },
        expected_actions=[], critical_actions=[],
        time_pressure_seconds=900, family_present=False,
        resources_available=["aspirin"],
    )
    clock = SimClock(0.0)
    engine = PhysiologyEngine(case, clock=clock)
    clock.advance(10.0)
    engine.tick(10.0)
    result = engine.administer_medication("aspirin")
    assert result.get("adverse") is True

    clock.advance(1.0)
    engine.tick(1.0)

    allergy_entries = [
        fh for fh in engine._flag_history
        if fh.get("set") and fh.get("cause") == "drug.allergy"
    ]
    assert len(allergy_entries) >= 1
    for ae in allergy_entries:
        assert ae["detail"].get("drug") == "aspirin"


def test_engine_pd_flags_carry_pharmacodynamic_cause():
    """PD-driven flags should carry PHARMACODYNAMIC or DRUG_THERAPEUTIC cause."""
    from shared.schemas.case import CaseBlueprint
    from services.pratibimb.app.physio.state import PhysiologyEngine, SimClock

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
        resources_available=["morphine"],
    )
    clock = SimClock(0.0)
    engine = PhysiologyEngine(case, clock=clock)
    clock.advance(5.0)
    engine.tick(5.0)
    engine.administer_medication("morphine", dose=20.0)

    for _ in range(200):
        clock.advance(1.0)
        engine.tick(1.0)

    flag_causes = {fh.get("cause") for fh in engine._flag_history if fh.get("set")}
    for cause in flag_causes:
        if cause is None:
            continue
        assert cause.startswith("drug.") or cause.startswith("physiology."), (
            f"Unexpected cause prefix: {cause!r}"
        )


def test_flag_clear_reason_is_resolved():
    """When pharm engine expires a flag, the clear reason should be RESOLVED."""
    from shared.schemas.case import CaseBlueprint
    from services.pratibimb.app.physio.state import PhysiologyEngine, SimClock

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
            "allergies": ["aspirin"],
            "social_history": {}, "red_herrings": [],
        },
        expected_actions=[], critical_actions=[],
        time_pressure_seconds=900, family_present=False,
        resources_available=["aspirin"],
    )
    clock = SimClock(0.0)
    engine = PhysiologyEngine(case, clock=clock)
    clock.advance(5.0)
    engine.tick(5.0)
    engine.administer_medication("aspirin")

    # Tick past the flag TTL (10 min in pharm time)
    for _ in range(700):
        clock.advance(1.0)
        engine.tick(1.0)

    clear_entries = [
        fh for fh in engine._flag_history if not fh.get("set")
    ]
    for entry in clear_entries:
        assert entry.get("reason") == "flag_expired_or_resolved"
        assert entry.get("cause") == "physiology.homeostasis"
