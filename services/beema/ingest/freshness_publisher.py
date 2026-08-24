from __future__ import annotations

"""
Publish SkillFreshnessSnapshot projections to Dhaara after BEEMA ingest.

Dhaara consumes snapshots — it never recomputes from raw ledger rows.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from services.beema.analytics.freshness import (
    competency_state_from_snapshot,
    snapshot_from_signal,
)
from services.beema.analytics.skill_decay import SkillDecaySignal, SkillDecayStore
from shared.schemas.freshness import CompetencyState, SkillFreshnessSnapshot


class FreshnessPublisher(Protocol):
    def publish(self, snapshot: SkillFreshnessSnapshot) -> None: ...


@dataclass
class InMemoryFreshnessPublisher:
    """Test/dev publisher that retains snapshots and derived competency state."""

    snapshots: list[SkillFreshnessSnapshot] = field(default_factory=list)
    states: dict[tuple[str, str], CompetencyState] = field(default_factory=dict)

    def publish(self, snapshot: SkillFreshnessSnapshot) -> None:
        self.snapshots.append(snapshot)
        state = competency_state_from_snapshot(snapshot)
        self.states[(state.learner_pseudo_id, state.competency_id)] = state


class FreshnessBridge:
    """
    Hooks SkillDecayUpdater.on_signal and emits stable snapshots downstream.
    """

    def __init__(
        self,
        publisher: FreshnessPublisher,
        *,
        store: Optional[SkillDecayStore] = None,
    ) -> None:
        self.publisher = publisher
        self.store = store
        self.snapshots: list[SkillFreshnessSnapshot] = []

    def on_signal(self, sig: SkillDecaySignal) -> None:
        sessions = (sig.source_session_id,)
        if self.store is not None:
            from services.beema.analytics.skill_decay import SkillDecayKey

            entry = self.store.get(SkillDecayKey(sig.learner_id, sig.competency_id))
            if entry is not None:
                sessions = entry.source_session_ids
        snap = snapshot_from_signal(sig, source_session_ids=sessions)
        self.snapshots.append(snap)
        self.publisher.publish(snap)

    def as_callback(self) -> Callable[[SkillDecaySignal], None]:
        return self.on_signal
