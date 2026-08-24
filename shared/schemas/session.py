from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from shared.schemas.case import CaseBlueprint, Language, Vital


class SessionStatus(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"


class AssessmentMode(str, Enum):
    """
    How much a session's grade is allowed to matter.

    PRACTICE and FORMATIVE tolerate a missing grading blueprint because nothing
    downstream stakes anything on the result. SUMMATIVE feeds the credential
    record, so an ungradable session must fail loudly rather than resolve to a
    silent absence that a learner could read as a pass.
    """

    PRACTICE = "practice"
    FORMATIVE = "formative"
    SUMMATIVE = "summative"

    @property
    def requires_grade(self) -> bool:
        return self is AssessmentMode.SUMMATIVE

    @property
    def rank(self) -> int:
        return _MODE_RANK[self]

    @classmethod
    def strictest(cls, *modes: "AssessmentMode") -> "AssessmentMode":
        """
        Highest-stakes mode among the arguments.

        A case declares its own stakes and so does the session that launches it.
        Resolving disagreement toward the stricter of the two means launching a
        summative case cannot quietly disable its grading requirement.
        """
        present = [m for m in modes if m is not None]
        if not present:
            return cls.PRACTICE
        return max(present, key=lambda m: m.rank)


_MODE_RANK: dict[AssessmentMode, int] = {
    AssessmentMode.PRACTICE: 0,
    AssessmentMode.FORMATIVE: 1,
    AssessmentMode.SUMMATIVE: 2,
}


class TranscriptTurn(BaseModel):
    role: str  # "student" | "patient" | "system"
    content: str
    language: Language | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionRecord(BaseModel):
    action: str
    t: float  # seconds since session start
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    session_id: str
    learner_id: str
    case: CaseBlueprint
    status: SessionStatus = SessionStatus.CREATED
    assessment_mode: AssessmentMode = AssessmentMode.PRACTICE
    language: Language = Language.MARATHI
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    action_log: list[ActionRecord] = Field(default_factory=list)
    current_vitals: Vital | None = None
    elapsed_seconds: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Phase E1: create-time snapshot for ledger finalize verify.
    tenant_id: str | None = None
    case_version: str | None = None
    blueprint_content_hash: str | None = None
    # Phase E2.2.c/e: provenance of the stamp. NULL = legacy / E1 escape hatch.
    blueprint_source: str | None = None
    # Exact corpus envelope bytes when case was loaded from projection (byte-equal assert).
    corpus_envelope_json: str | None = None


class CreateSessionRequest(BaseModel):
    learner_id: str
    target_difficulty: float = 0.5
    state: str | None = None
    language: Language = Language.MARATHI
    learner_gaps: dict[str, float] | None = None
    assessment_mode: AssessmentMode = AssessmentMode.PRACTICE
    # tenant_id alone → tenant-scoped sample. Full triple → published pick.
    tenant_id: str | None = None
    case_id: str | None = None
    case_version: str | None = None

    @model_validator(mode="after")
    def _published_pick_all_or_none(self) -> CreateSessionRequest:
        # Explicit pick requires all three. tenant_id alone is allowed for sampling.
        if self.case_id is not None or self.case_version is not None:
            if not all(
                v is not None
                for v in (self.tenant_id, self.case_id, self.case_version)
            ):
                raise PydanticCustomError(
                    "invalid_case_pick",
                    "tenant_id, case_id, and case_version must be provided together",
                )
        return self


class StudentUtteranceRequest(BaseModel):
    utterance: str
    language: Language | None = None


class ClinicalActionRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    session_id: str
    status: SessionStatus
    case_id: str
    patient_name: str
    chief_complaint: str
    language: Language
    vitals: Vital | None = None
    elapsed_seconds: float = 0.0
    time_pressure_seconds: int = 900
    family_present: bool = False
    resources_available: list[str] = Field(default_factory=list)


class PatientResponsePayload(BaseModel):
    utterance: str
    prosody: dict[str, float] = Field(default_factory=dict)
    comprehension: float = 0.7
    family_interjection: str | None = None
    audio_url: str | None = None


class DialogueResponse(BaseModel):
    patient: PatientResponsePayload
    vitals: Vital
    elapsed_seconds: float
    session_status: SessionStatus
