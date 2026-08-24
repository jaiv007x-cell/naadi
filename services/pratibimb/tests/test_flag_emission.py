"""Tests for flag emission — set_flag/clear_flag on legacy PhysioTrace."""

from __future__ import annotations

from shared.schemas.flag_cause import FlagCause
from services.pratibimb.app.physio.state import PhysioTrace


def _trace() -> PhysioTrace:
    return PhysioTrace()


def test_set_flag_emits_flag_raised_event():
    t = _trace()
    t.set_flag("hypotension", t=100.0, ttl_s=180, cause=FlagCause.DRUG_PD_EFFECT)
    raised = [e for e in t.events if e.get("kind") == "flag_raised"]
    assert len(raised) == 1
    assert raised[0]["flag"] == "hypotension"
    assert raised[0]["ttl_s"] == 180
    assert raised[0]["cause"] == "drug.pd_effect"
    assert raised[0]["t"] == 100.0


def test_reraise_active_flag_is_idempotent():
    t = _trace()
    t.set_flag("hypotension", t=100.0, ttl_s=60, cause=FlagCause.CLINICAL_HYPOTENSION)
    t.set_flag("hypotension", t=110.0, ttl_s=120, cause=FlagCause.CLINICAL_HYPOTENSION)
    raised = [e for e in t.events if e.get("kind") == "flag_raised"]
    assert len(raised) == 1
    assert t.active_flags["hypotension"] == 110.0 + 120


def test_clear_flag_emits_flag_cleared():
    t = _trace()
    t.set_flag("hypotension", t=100.0, cause=FlagCause.CLINICAL_HYPOTENSION)
    t.clear_flag("hypotension", t=200.0, cause=FlagCause.PHYSIOLOGY_COMPENSATION)
    cleared = [e for e in t.events if e.get("kind") == "flag_cleared"]
    assert len(cleared) == 1
    assert cleared[0]["cause"] == "physiology.compensation"
    assert cleared[0]["t"] == 200.0


def test_ttl_clear_emits_flag_expired():
    t = _trace()
    t.set_flag("bronchospasm", t=100.0, ttl_s=30, cause=FlagCause.CLINICAL_AIRWAY_COMPROMISE)
    t.clear_flag("bronchospasm", t=131.0, cause=FlagCause.PHYSIOLOGY_TIMEOUT)
    expired = [e for e in t.events if e.get("kind") == "flag_expired"]
    cleared = [e for e in t.events if e.get("kind") == "flag_cleared"]
    assert len(expired) == 1
    assert len(cleared) == 0


def test_clear_nonexistent_flag_is_noop():
    t = _trace()
    t.clear_flag("never_raised", t=50.0)
    assert not [e for e in t.events if e.get("kind") in ("flag_cleared", "flag_expired")]
    assert len(t.flag_history) == 0


def test_recovery_velocity_computable_from_events():
    t = _trace()
    t.set_flag("hypotension", t=100.0, cause=FlagCause.CLINICAL_HYPOTENSION)
    t.clear_flag("hypotension", t=145.0, cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS)
    raised = next(e for e in t.events if e["kind"] == "flag_raised")
    cleared = next(e for e in t.events if e["kind"] == "flag_cleared")
    assert cleared["t"] - raised["t"] == 45.0


def test_flag_history_carries_cause_and_namespace():
    t = _trace()
    t.set_flag("hypoxia", t=10.0, cause=FlagCause.CLINICAL_HYPOXIA)
    t.clear_flag("hypoxia", t=50.0, cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS)
    assert t.flag_history[0]["cause"] == "clinical.hypoxia"
    assert t.flag_history[0]["namespace"] == "clinical"
    assert t.flag_history[1]["cause"] == "physiology.homeostasis"
