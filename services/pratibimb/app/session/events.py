from __future__ import annotations

from enum import Enum


class SessionEvent(str, Enum):
    CREATED = "session.created"
    STARTED = "session.started"
    STUDENT_UTTERANCE = "session.student_utterance"
    PATIENT_RESPONSE = "session.patient_response"
    CLINICAL_ACTION = "session.clinical_action"
    VITALS_TICK = "session.vitals_tick"
    TIME_PRESSURE = "session.time_pressure"
    COMPLETED = "session.completed"
    ABORTED = "session.aborted"
    SCORED = "session.scored"
    DHAARA_UPDATE = "session.dhaara_update"


def event_payload(event: SessionEvent, session_id: str, data: dict | None = None) -> dict:
    return {
        "event_type": event.value,
        "session_id": session_id,
        "data": data or {},
    }
