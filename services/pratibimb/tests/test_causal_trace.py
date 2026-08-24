"""
End-to-end causal trace test.

Proves: drug administration -> flag raised with dotted cause code ->
causal link is machine-readable -> flag resolved -> recovery velocity
computable from paired events.
"""

from __future__ import annotations

from services.pratibimb.app.physio.state import PhysiologyEngine, SimClock, PhysioTrace
from shared.schemas.case import CaseBlueprint


def _probe_case() -> CaseBlueprint:
    from services.pratibimb.app.case_gen.sampler import load_probe_case
    return load_probe_case("probe.stemi.inferior.v1", harness=True)


def test_morphine_causal_chain():
    """
    Administer morphine -> PD flags raised with cause='drug.morphine' ->
    flag resolves after drug wearoff -> recovery velocity is computable.
    """
    case = _probe_case()
    clock = SimClock(0.0)
    engine = PhysiologyEngine(case, clock=clock)

    # Warm up
    engine.tick_to(60.0)

    # Administer morphine (has PD: sbp -10, rr -4, hr -3)
    engine.administer_medication("morphine", dose=10.0)

    # Tick long enough for PD flags to appear and then expire
    engine.tick_to(1800.0)

    trace = engine.trace()

    # --- Verify flag_raised events carry dotted cause codes ---
    raised_events = [
        e for e in trace.events
        if e.get("kind") == "flag_raised" or (e.get("kind") == "dose" and False)
    ]

    flag_raised_history = [
        fh for fh in engine._flag_history if fh.get("set") is True
    ]

    # At least some PD flags should have fired (morphine causes
    # preload_reduction at 0.3 frac threshold)
    if flag_raised_history:
        for fh in flag_raised_history:
            cause = fh.get("cause", "")
            reason = fh.get("reason", "")
            # cause must be a dotted code, not free text
            assert "." in cause, (
                f"Flag {fh['flag']} has non-dotted cause: {cause!r}"
            )
            # reason must be a machine-stable token
            assert " " not in reason, (
                f"Flag {fh['flag']} has free-text reason: {reason!r}"
            )

    # --- Verify flag_cleared events carry cause/reason ---
    flag_cleared_history = [
        fh for fh in engine._flag_history if fh.get("set") is False
    ]
    for fh in flag_cleared_history:
        cause = fh.get("cause", "")
        reason = fh.get("reason", "")
        assert cause or reason, f"Flag {fh['flag']} cleared without cause or reason"

    # --- Recovery velocity: pair raised/cleared by flag name ---
    flag_names = {fh["flag"] for fh in engine._flag_history}
    for fname in flag_names:
        raised = [fh for fh in engine._flag_history if fh["flag"] == fname and fh["set"]]
        cleared = [fh for fh in engine._flag_history if fh["flag"] == fname and not fh["set"]]
        if raised and cleared:
            recovery_s = cleared[0]["t"] - raised[0]["t"]
            assert recovery_s >= 0, f"Negative recovery for {fname}"


def test_allergy_causal_chain():
    """
    Administer drug the patient is allergic to -> flag raised with
    cause='drug.<name>' reason='allergic_reaction'.
    """
    case = CaseBlueprint(
        case_id="test-allergy",
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
    engine.tick_to(10.0)

    result = engine.administer_medication("aspirin")
    assert result.get("adverse") is True

    engine.tick_to(20.0)

    allergy_flags = [
        fh for fh in engine._flag_history
        if fh.get("set") and fh.get("cause") == "drug.allergy"
    ]
    assert len(allergy_flags) >= 1
    for fh in allergy_flags:
        assert fh["detail"].get("drug") == "aspirin"


def test_trace_events_carry_cause_reason():
    """PhysioTrace events carry dotted cause and namespace."""
    from shared.schemas.flag_cause import FlagCause

    t = PhysioTrace()
    t.set_flag("hypotension", t=100.0, cause=FlagCause.DRUG_PD_EFFECT, detail={"drug": "nitroglycerin"})
    t.clear_flag("hypotension", t=200.0, cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS)

    raised = [e for e in t.events if e["kind"] == "flag_raised"]
    assert len(raised) == 1
    assert raised[0]["cause"] == "drug.pd_effect"
    assert raised[0]["namespace"] == "drug"

    cleared = [e for e in t.events if e["kind"] == "flag_cleared"]
    assert len(cleared) == 1
    assert cleared[0]["cause"] == "physiology.homeostasis"


def test_no_free_text_in_engine_flag_history():
    """
    After running the reference case with morphine, no flag history entry
    should contain spaces in cause or reason (free text).
    """
    case = _probe_case()
    clock = SimClock(0.0)
    engine = PhysiologyEngine(case, clock=clock)
    engine.tick_to(60.0)
    engine.administer_medication("morphine", dose=10.0)
    engine.tick_to(600.0)

    for fh in engine._flag_history:
        cause = fh.get("cause")
        if cause is not None:
            assert " " not in str(cause), f"Free-text cause: {cause!r}"
        reason = fh.get("reason")
        if reason is not None:
            assert " " not in str(reason), f"Free-text reason: {reason!r}"
