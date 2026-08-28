"""
Dhaara Freshness v1 — derived signals from frozen ledger evidence.

Dhaara must NOT recompute decay from raw ledger rows. It consumes stable
SkillFreshnessSnapshot projections emitted after BEEMA Skill Decay ingest.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


FRESHNESS_BASIS_V1 = "policy_prior_v1"


class SkillFreshnessSnapshot(BaseModel):
    """
    Immutable derived unit emitted once per (session, competency) ingest.
    Links back to ledger provenance via source_session_ids and replay hash.
    """

    model_config = ConfigDict(frozen=True)

    learner_pseudo_id: str
    competency_id: str
    competency_before: Optional[float] = None
    competency_after: float = Field(ge=0.0, le=1.0)
    ability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    freshness: float = Field(ge=0.0, le=1.0)
    evidence_age_days: float = Field(ge=0.0)
    reassessment_due: bool
    basis: str = FRESHNESS_BASIS_V1
    source_session_ids: tuple[str, ...]
    source_replay_hash: str
    computed_at: datetime
    evidence_class: Optional[str] = None
    evidence_weight: Optional[float] = Field(default=None, ge=0.0)


class CompetencyState(BaseModel):
    """
    Dhaara's current view of one learner × competency.

    ability, confidence, and freshness are intentionally separate:
    a learner can retain high estimated ability while evidence goes stale.
    """

    model_config = ConfigDict(frozen=True)

    learner_pseudo_id: str
    competency_id: str
    ability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    freshness: float = Field(ge=0.0, le=1.0)
    evidence_age_days: float = Field(ge=0.0)
    reassessment_due: bool
    basis: str = FRESHNESS_BASIS_V1
    last_session_id: str
    source_session_ids: tuple[str, ...]
    updated_at: datetime

    def display_label(self) -> str:
        return self.competency_id.replace("_", " ").replace(".", " ").title()
