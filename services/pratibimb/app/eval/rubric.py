"""
Session-level competency rubric, plus a re-export of the case grading contract.

The grading types (`Axis`, `Severity`, `RubricHit`, `GradingBlueprint`) moved to
`shared.schemas.grading` when `CaseBlueprintV2` began embedding a blueprint: a
case object in `shared/` cannot import from a service package. They are
re-exported here so grader and rubric modules can keep importing from one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field

from shared.schemas.grading import (
    NIRIKSHAK_VERSION,
    Axis,
    GradingBlueprint,
    RubricHit,
    Severity,
    letter_for,
)

__all__ = [
    "NIRIKSHAK_VERSION",
    "Axis",
    "GradingBlueprint",
    "RubricHit",
    "Severity",
    "letter_for",
    "Competency",
    "RubricDimension",
    "RUBRIC",
    "RubricScore",
]


# ── Session-level competency rubric (scorer.py) ───────────────────────────────

class Competency(str, Enum):
    CLINICAL_JUDGMENT = "clinical_judgment"
    PHARMACOLOGICAL = "pharmacological"
    COMMUNICATION = "communication"
    STRESS_RESPONSE = "stress_response"
    PROCEDURAL = "procedural"


@dataclass(frozen=True)
class RubricDimension:
    weight: float
    description: str


RUBRIC: dict[str, RubricDimension] = {
    Competency.CLINICAL_JUDGMENT.value: RubricDimension(
        weight=0.25, description="Correct orders, timing, red-flag recognition"
    ),
    Competency.PHARMACOLOGICAL.value: RubricDimension(
        weight=0.15, description="Drug selection, dosing, contraindication awareness, reversal"
    ),
    Competency.COMMUNICATION.value: RubricDimension(
        weight=0.25, description="Empathy, open questions, language match"
    ),
    Competency.STRESS_RESPONSE.value: RubricDimension(
        weight=0.15, description="Calm reaction during physio crisis window"
    ),
    Competency.PROCEDURAL.value: RubricDimension(
        weight=0.20, description="Procedure sequence adherence"
    ),
}


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
