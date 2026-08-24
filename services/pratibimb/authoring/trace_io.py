"""Deserialize PhysioTrace fixtures stored as JSON."""
from __future__ import annotations

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


def trace_from_json(payload: dict) -> PhysioTrace:
    """Rebuild a typed trace from canonical JSON (inverse of to_canonical)."""
    trace = PhysioTrace(
        case_id=payload.get("case_id", ""),
        case_version=payload.get("case_version", "0.0.0"),
    )
    for drug in payload.get("drugs", []):
        trace.record_drug(DrugAdminEvent(**drug))
    for flag in payload.get("flags", []):
        trace.record_flag(FlagEvent(**flag))
    for order in payload.get("orders", []):
        order_data = dict(order)
        params = order_data.pop("params", {}) or {}
        trace.record_order(OrderEvent(params=params, **order_data))
    for dx in payload.get("dx", []):
        trace.record_diagnosis(DiagnosisEvent(**dx))
    for recog in payload.get("recog", []):
        trace.record_recognition(RecognitionEvent(**recog))
    for escal in payload.get("escal", []):
        trace.record_escalation(EscalationEvent(**escal))
    for handoff in payload.get("handoffs", []):
        fields = handoff.get("fields", [])
        trace.record_handoff(
            HandoffEvent(
                fields=frozenset(fields),
                target=handoff["target"],
                t_s=handoff["t_s"],
            )
        )
    return trace
