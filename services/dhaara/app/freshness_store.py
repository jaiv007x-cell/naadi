from __future__ import annotations

"""
Dhaara Freshness v1 — competency state derived from SkillFreshnessSnapshot only.

Dhaara never recomputes decay from raw ledger rows.
"""
from dataclasses import dataclass, field

from services.beema.analytics.freshness import competency_state_from_snapshot
from shared.schemas.freshness import CompetencyState, SkillFreshnessSnapshot


@dataclass
class FreshnessStore:
    """In-memory store; replace with durable projection in production."""

    states: dict[tuple[str, str], CompetencyState] = field(default_factory=dict)
    snapshots: list[SkillFreshnessSnapshot] = field(default_factory=list)

    def ingest(self, snapshot: SkillFreshnessSnapshot) -> CompetencyState:
        self.snapshots.append(snapshot)
        state = competency_state_from_snapshot(snapshot)
        key = (state.learner_pseudo_id, state.competency_id)
        self.states[key] = state
        return state

    def ingest_many(self, snapshots: tuple[SkillFreshnessSnapshot, ...]) -> tuple[CompetencyState, ...]:
        return tuple(self.ingest(s) for s in snapshots)

    def get_state(self, learner_pseudo_id: str, competency_id: str) -> CompetencyState | None:
        return self.states.get((learner_pseudo_id, competency_id))

    def list_states(self, learner_pseudo_id: str) -> tuple[CompetencyState, ...]:
        return tuple(
            s for (lid, _), s in sorted(self.states.items()) if lid == learner_pseudo_id
        )
