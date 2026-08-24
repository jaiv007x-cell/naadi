from __future__ import annotations

from pydantic import BaseModel, Field


class RubricScore(BaseModel):
    clinical_reasoning: float = Field(ge=0, le=10)
    procedural_correctness: float = Field(ge=0, le=10)
    communication: float = Field(ge=0, le=10)
    ethical_legal: float = Field(ge=0, le=10)
    stress_modulated: float = Field(ge=0, le=10)
    notes: list[str] = Field(default_factory=list)

    @property
    def overall(self) -> float:
        return round(
            0.30 * self.clinical_reasoning
            + 0.20 * self.procedural_correctness
            + 0.25 * self.communication
            + 0.10 * self.ethical_legal
            + 0.15 * self.stress_modulated,
            2,
        )


class CompetencyUpdate(BaseModel):
    learner_id: str
    competency_tag: str
    delta: float
    evidence: str = ""


class SessionScoreReport(BaseModel):
    session_id: str
    learner_id: str
    case_id: str
    rubric: RubricScore
    critical_actions_hit: list[str]
    critical_actions_missed: list[str]
    competency_updates: list[CompetencyUpdate] = Field(default_factory=list)
