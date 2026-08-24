"""
Evidence timeline projection — read-only view over ledger-derived events.

Each row links to underlying session provenance; no duplicated mutable state.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas.freshness import SkillFreshnessSnapshot


class TimelineEventKind(str, Enum):
    SIMULATION_PASSED = "simulation.passed"
    SIMULATION_FAILED = "simulation.failed"
    COMPETENCY_CONFIRMED = "competency.confirmed"
    FRESHNESS_THRESHOLD_CROSSED = "freshness.threshold_crossed"
    REASSESSMENT_DUE = "reassessment.due"
    FRESHNESS_RESTORED = "freshness.restored"
    CONFIDENCE_INCREASED = "confidence.increased"


class TimelineEntry(BaseModel):
    """One row in a clinician evidence timeline."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: TimelineEventKind
    summary: str
    learner_pseudo_id: str
    competency_id: Optional[str] = None
    session_id: Optional[str] = None
    replay_hash: Optional[str] = None
    delta_ability: Optional[float] = None
    delta_confidence: Optional[float] = None
    freshness: Optional[float] = None
    basis: Optional[str] = None


FRESHNESS_STALE_THRESHOLD = 0.70


def project_snapshot_to_timeline(
    snapshot: SkillFreshnessSnapshot,
    *,
    passed: bool = True,
) -> list[TimelineEntry]:
    """Append timeline rows from a freshness snapshot (pure projection)."""
    entries: list[TimelineEntry] = []
    sid = snapshot.source_session_ids[-1]

    if passed:
        delta = None
        if snapshot.competency_before is not None:
            delta = snapshot.competency_after - snapshot.competency_before
        entries.append(
            TimelineEntry(
                at=snapshot.computed_at,
                kind=TimelineEventKind.SIMULATION_PASSED,
                summary=f"Simulation completed — {snapshot.competency_id}",
                learner_pseudo_id=snapshot.learner_pseudo_id,
                competency_id=snapshot.competency_id,
                session_id=sid,
                replay_hash=snapshot.source_replay_hash,
                delta_ability=delta,
                freshness=snapshot.freshness,
                basis=snapshot.basis,
            )
        )

    if snapshot.reassessment_due:
        entries.append(
            TimelineEntry(
                at=snapshot.computed_at,
                kind=TimelineEventKind.REASSESSMENT_DUE,
                summary="Reassessment due — evidence freshness policy threshold",
                learner_pseudo_id=snapshot.learner_pseudo_id,
                competency_id=snapshot.competency_id,
                session_id=sid,
                replay_hash=snapshot.source_replay_hash,
                freshness=snapshot.freshness,
                basis=snapshot.basis,
            )
        )
    elif snapshot.freshness < FRESHNESS_STALE_THRESHOLD:
        entries.append(
            TimelineEntry(
                at=snapshot.computed_at,
                kind=TimelineEventKind.FRESHNESS_THRESHOLD_CROSSED,
                summary=f"No new evidence — freshness crossed {FRESHNESS_STALE_THRESHOLD:.2f}",
                learner_pseudo_id=snapshot.learner_pseudo_id,
                competency_id=snapshot.competency_id,
                session_id=sid,
                freshness=snapshot.freshness,
                basis=snapshot.basis,
            )
        )

    if snapshot.competency_before is not None and snapshot.competency_after > snapshot.competency_before:
        entries.append(
            TimelineEntry(
                at=snapshot.computed_at,
                kind=TimelineEventKind.FRESHNESS_RESTORED,
                summary="Repeat practice — freshness restored",
                learner_pseudo_id=snapshot.learner_pseudo_id,
                competency_id=snapshot.competency_id,
                session_id=sid,
                replay_hash=snapshot.source_replay_hash,
                freshness=snapshot.freshness,
                basis=snapshot.basis,
            )
        )

    return entries
