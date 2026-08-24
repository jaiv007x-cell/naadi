"""Tests for the typed trace contract in shared.schemas.trace."""
from __future__ import annotations

import json

import pytest

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


@pytest.fixture
def trace() -> PhysioTrace:
    return PhysioTrace(case_id="CASE_1", case_version="1.0.0")


# ── recording and duration ───────────────────────────────────────────────────

def test_empty_trace_has_zero_duration(trace):
    assert trace.duration_s == 0.0
    assert trace.drug_admins() == []
    assert trace.flag_events() == []


def test_duration_tracks_latest_event(trace):
    trace.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=60))
    trace.record_order(OrderEvent("oxygen", t_s=240))
    trace.record_flag(FlagEvent("stridor", True, t_s=120))
    assert trace.duration_s == 240


def test_duration_ignores_earlier_events(trace):
    trace.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=300))
    trace.record_drug(DrugAdminEvent("hydrocortisone", 200, "mg", "IV", t_s=100))
    assert trace.duration_s == 300


# ── drug queries ─────────────────────────────────────────────────────────────

def test_drug_admins_filters_by_id(trace):
    trace.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=60))
    trace.record_drug(DrugAdminEvent("pheniramine", 25, "mg", "IV", t_s=120))
    assert len(trace.drug_admins("adrenaline")) == 1
    assert len(trace.drug_admins()) == 2


def test_drug_admins_normalizes_names(trace):
    trace.record_drug(DrugAdminEvent("Normal-Saline", 500, "mL", "IV", t_s=60))
    assert len(trace.drug_admins("normal_saline")) == 1
    assert len(trace.drug_admins("NORMALSALINE")) == 1


def test_drug_admins_unknown_drug_is_empty(trace):
    trace.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=60))
    assert trace.drug_admins("metoprolol") == []


# ── flag queries ─────────────────────────────────────────────────────────────

def test_flag_set_and_cleared_at(trace):
    trace.record_flag(FlagEvent("shock", True, t_s=30))
    trace.record_flag(FlagEvent("shock", False, t_s=90))
    assert trace.flag_set_at("shock") == 30
    assert trace.flag_cleared_at("shock") == 90
    assert trace.flag_set_at("never") is None


def test_flag_spans_closed_interval(trace):
    trace.record_flag(FlagEvent("shock", True, t_s=30))
    trace.record_flag(FlagEvent("shock", False, t_s=90))
    assert trace.flag_spans("shock") == [(30, 90)]


def test_flag_spans_open_span_closes_at_end(trace):
    trace.record_flag(FlagEvent("shock", True, t_s=30))
    trace.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=200))
    assert trace.flag_spans("shock") == [(30, 200)]


def test_flag_spans_multiple_cycles(trace):
    for t, active in ((10, True), (20, False), (40, True), (60, False)):
        trace.record_flag(FlagEvent("shock", active, t_s=t))
    assert trace.flag_spans("shock") == [(10, 20), (40, 60)]


def test_flag_active_at(trace):
    trace.record_flag(FlagEvent("shock", True, t_s=30))
    trace.record_flag(FlagEvent("shock", False, t_s=90))
    assert trace.flag_active_at("shock", 60) is True
    assert trace.flag_active_at("shock", 120) is False


# ── other queries ────────────────────────────────────────────────────────────

def test_orders_case_insensitive(trace):
    trace.record_order(OrderEvent("ECG_12L", t_s=100))
    assert len(trace.orders("ecg_12l")) == 1


def test_order_params_default_to_empty_dict(trace):
    trace.record_order(OrderEvent("oxygen", t_s=100))
    assert trace.orders("oxygen")[0].params == {}


def test_order_params_preserved(trace):
    trace.record_order(OrderEvent("oxygen", t_s=100, params={"lpm": 8}))
    assert trace.orders("oxygen")[0].params == {"lpm": 8}


def test_diagnoses_and_recognitions(trace):
    trace.record_diagnosis(DiagnosisEvent("anaphylaxis", 0.9, t_s=150))
    trace.record_recognition(RecognitionEvent("vital", "spo2_low", t_s=60))
    trace.record_recognition(RecognitionEvent("physical_sign", "stridor", t_s=80))
    assert len(trace.diagnoses("anaphylaxis")) == 1
    assert len(trace.recognitions(kind="vital")) == 1
    assert len(trace.recognitions(kind="vital", token="spo2_low")) == 1
    assert len(trace.recognitions(kind="physical_sign")) == 1
    assert trace.recognitions(kind="finding") == []


def test_escalations_and_handoffs(trace):
    trace.record_escalation(EscalationEvent("physician_on_call", t_s=200))
    trace.record_handoff(HandoffEvent(frozenset({"situation"}), "icu", t_s=400))
    assert len(trace.escalations("physician_on_call")) == 1
    assert len(trace.escalations("code_blue")) == 0
    assert len(trace.handoffs("icu")) == 1


# ── canonical serialization ──────────────────────────────────────────────────

def test_to_canonical_is_json_serializable(trace):
    trace.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=60))
    trace.record_flag(FlagEvent("shock", True, t_s=30))
    trace.record_handoff(HandoffEvent(frozenset({"situation", "background"}), "icu", t_s=400))
    blob = json.dumps(trace.to_canonical(), sort_keys=True)
    assert "adrenaline" in blob


def test_to_canonical_is_stable_across_calls(trace):
    trace.record_drug(DrugAdminEvent("adrenaline", 0.5, "mg", "IM", t_s=60))
    trace.record_handoff(HandoffEvent(frozenset({"b", "a"}), "icu", t_s=400))
    first = json.dumps(trace.to_canonical(), sort_keys=True)
    second = json.dumps(trace.to_canonical(), sort_keys=True)
    assert first == second


def test_to_canonical_sorts_handoff_fields(trace):
    trace.record_handoff(HandoffEvent(frozenset({"recommendation", "situation"}), "icu", t_s=1))
    assert trace.to_canonical()["handoffs"][0]["fields"] == ["recommendation", "situation"]


# ── legacy bridge ────────────────────────────────────────────────────────────

def test_from_legacy_maps_events_and_flags():
    from services.pratibimb.app.physio.state import PhysioTrace as LegacyTrace

    legacy = LegacyTrace()
    legacy.events = [
        {"kind": "dose", "drug": "morphine", "amount_mg": 4.0, "route": "iv_bolus", "t": 120},
        {"kind": "order", "t": 60, "payload": {"order": "oxygen", "lpm": 8}},
        {"kind": "diagnosis", "t": 90, "payload": {"dx": "anaphylaxis", "confidence": 0.8}},
        {"kind": "escalation", "t": 200, "payload": {"to": "physician_on_call"}},
        {"kind": "noise_we_do_not_grade", "t": 10},
    ]
    legacy.flag_history = [{"flag": "shock", "set": True, "t": 55}]

    typed, report = legacy.to_typed_trace(case_id="C1", case_version="2.0.0")

    assert typed.case_id == "C1"
    assert typed.case_version == "2.0.0"
    assert len(typed.drug_admins("morphine")) == 1
    assert typed.drug_admins("morphine")[0].dose == 4.0
    assert typed.drug_admins("morphine")[0].route == "IV_BOLUS"
    assert typed.orders("oxygen")[0].params == {"lpm": 8}
    assert typed.diagnoses("anaphylaxis")[0].confidence == 0.8
    assert len(typed.escalations("physician_on_call")) == 1
    assert typed.flag_set_at("shock") == 55
    assert report.dropped["noise_we_do_not_grade"] == 1


def test_from_legacy_converts_minutes_to_seconds():
    from services.pratibimb.app.physio.state import PhysioTrace as LegacyTrace

    legacy = LegacyTrace()
    legacy.events = [{"kind": "dose", "drug": "aspirin", "amount_mg": 325, "t_min": 2.0}]
    typed, report = legacy.to_typed_trace()
    assert typed.drug_admins("aspirin")[0].t_s == 120.0
    assert report.inferred["events[0].t_s"] == "t_min * 60"


def test_from_legacy_on_empty_trace():
    from services.pratibimb.app.physio.state import PhysioTrace as LegacyTrace

    typed, report = LegacyTrace().to_typed_trace()
    assert typed.duration_s == 0.0
    assert typed.drug_admins() == []
    assert report.is_lossless


# ── migration audit ──────────────────────────────────────────────────────────

def _legacy(events=None, flags=None):
    from services.pratibimb.app.physio.state import PhysioTrace as LegacyTrace

    legacy = LegacyTrace()
    legacy.events = events or []
    legacy.flag_history = flags or []
    return legacy


def test_dropped_events_are_counted_by_kind():
    legacy = _legacy([
        {"kind": "avatar_frame", "t": 1},
        {"kind": "avatar_frame", "t": 2},
        {"kind": "ui_focus", "t": 3},
        {"kind": "deprecated_physio_nudge", "t": 4},
    ])
    _, report = legacy.to_typed_trace()
    assert report.total_dropped == 4
    assert report.dropped["avatar_frame"] == 2
    assert report.is_lossless is False


def test_audit_line_names_every_dropped_kind():
    legacy = _legacy([
        {"kind": "avatar_frame", "t": 1},
        {"kind": "avatar_frame", "t": 2},
        {"kind": "ui_focus", "t": 3},
        {"kind": "deprecated_physio_nudge", "t": 4},
    ])
    _, report = legacy.to_typed_trace()
    line = report.audit_line()
    assert line.startswith("Dropped: 4")
    for kind in ("avatar_frame: 2", "ui_focus: 1", "deprecated_physio_nudge: 1"):
        assert kind in line


def test_lossless_migration_reports_zero_drops():
    legacy = _legacy([{"kind": "order", "t": 10, "payload": {"order": "ecg"}}])
    _, report = legacy.to_typed_trace()
    assert report.audit_line() == "Dropped: 0"
    assert report.consumed["order"] == 1


def test_events_without_a_kind_are_dropped_as_unspecified():
    _, report = _legacy([{"t": 5}]).to_typed_trace()
    assert report.dropped["unspecified"] == 1


def test_inferred_and_defaulted_are_tracked_separately():
    """A derived value and an invented value are different levels of trust."""
    legacy = _legacy([
        {"kind": "dose", "drug": "aspirin", "amount_mg": 325, "t_min": 1.0},
        {"kind": "diagnosis", "payload": {"dx": "stemi"}, "t": 90},
    ])
    _, report = legacy.to_typed_trace(case_id="C1", case_version="1.0.0")

    assert "events[0].t_s" in report.inferred          # derived from t_min
    assert "events[1].confidence" in report.defaulted  # invented by the schema
    assert "events[0].t_s" not in report.defaulted
    assert not set(report.inferred) & set(report.defaulted)
    assert report.needs_review is True


def test_missing_case_metadata_is_flagged_for_review():
    _, report = _legacy().to_typed_trace()
    assert "case_id" in report.defaulted
    assert "case_version" in report.defaulted
    assert report.needs_review is True


def test_supplied_case_metadata_needs_no_review():
    _, report = _legacy().to_typed_trace(case_id="C1", case_version="1.0.0")
    assert report.needs_review is False


def test_report_summary_splits_inferred_from_defaulted():
    legacy = _legacy([
        {"kind": "dose", "drug": "aspirin", "amount_mg": 325, "t_min": 1.0},
        {"kind": "diagnosis", "payload": {"dx": "stemi"}, "t": 90},
    ])
    _, report = legacy.to_typed_trace(case_id="C1", case_version="1.0.0")
    summary = report.summary()
    # t_s and dose_unit are inferred; the absent route and confidence are defaulted.
    assert "Inferred from source: 2" in summary
    assert "Defaulted (needs review): 2" in summary
    assert "review: events[1].confidence" in summary
    assert "review: events[0].route" in summary


def test_report_to_dict_is_serializable():
    import json

    legacy = _legacy([{"kind": "avatar_frame", "t": 1}])
    _, report = legacy.to_typed_trace()
    blob = json.dumps(report.to_dict())
    assert "avatar_frame" in blob


def test_migration_unpacks_and_attribute_accesses():
    migration = _legacy([{"kind": "order", "t": 1, "payload": {"order": "ecg"}}]).to_typed_trace()
    trace, report = migration
    assert trace is migration.trace
    assert report is migration.report


def test_migration_exposes_event_counts():
    migration = _legacy([
        {"kind": "drug", "drug": "aspirin", "amount_mg": 325, "route": "PO", "t": 12},
        {"kind": "avatar_frame", "t": 12.1},
        {"kind": "avatar_frame", "t": 12.2},
        {"kind": "ui_focus", "t": 13},
    ], flags=[{"flag": "ecg_ordered", "set": True, "t": 15}]).to_typed_trace()

    assert migration.accepted_events == 2  # one drug, one flag
    assert migration.dropped_events == 3
    assert migration.dropped_kinds == {"avatar_frame": 2, "ui_focus": 1}


# ── strict mode ──────────────────────────────────────────────────────────────

def test_strict_raises_on_an_unknown_kind():
    from shared.schemas.trace import UnknownLegacyEventKind

    with pytest.raises(UnknownLegacyEventKind) as exc:
        _legacy([{"kind": "avatar_frame", "t": 0}]).to_typed_trace(strict=True)
    assert exc.value.kind == "avatar_frame"


def test_strict_is_a_valueerror_for_generic_callers():
    with pytest.raises(ValueError):
        _legacy([{"kind": "avatar_frame", "t": 0}]).to_typed_trace(strict=True)


def test_strict_accepts_a_fully_recognized_stream():
    trace, report = _legacy(
        [{"kind": "order", "t": 10, "payload": {"order": "ecg"}}],
        flags=[{"flag": "shock", "set": True, "t": 20}],
    ).to_typed_trace(strict=True)
    assert report.is_lossless
    assert len(trace.orders("ecg")) == 1


def test_non_strict_is_the_default_for_live_sessions():
    """UI chatter in a live legacy stream is expected and must not raise."""
    _, report = _legacy([{"kind": "avatar_frame", "t": 0}]).to_typed_trace()
    assert report.dropped["avatar_frame"] == 1
