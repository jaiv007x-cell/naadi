"""
Project Skill Decay policy signals into Dhaara-ready freshness snapshots.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.beema.analytics.skill_decay import SkillDecaySignal
from shared.schemas.freshness import (
    FRESHNESS_BASIS_V1,
    CompetencyState,
    SkillFreshnessSnapshot,
)


def _clamp_unit(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _confidence_from_signal(sig: SkillDecaySignal) -> float:
    """Map shrinkage lower bound into a 0..1 confidence scalar."""
    if sig.effective_score <= 1e-6:
        return 0.0
    band = sig.effective_score - sig.confidence_lower
    return _clamp_unit(1.0 - band / sig.effective_score)


def snapshot_from_signal(
    sig: SkillDecaySignal,
    *,
    source_session_ids: tuple[str, ...] | None = None,
    computed_at: datetime | None = None,
) -> SkillFreshnessSnapshot:
    """Derive a stable snapshot; Dhaara ingests this without touching ledger rows."""
    sessions = source_session_ids or (sig.source_session_id,)
    now = computed_at or datetime.now(timezone.utc)
    return SkillFreshnessSnapshot(
        learner_pseudo_id=sig.learner_id,
        competency_id=sig.competency_id,
        competency_before=sig.competency_before,
        competency_after=sig.competency_after,
        ability=_clamp_unit(sig.effective_score),
        confidence=_confidence_from_signal(sig),
        freshness=_clamp_unit(sig.freshness),
        evidence_age_days=max(0.0, sig.evidence_age_days),
        reassessment_due=sig.reassessment_due,
        basis=sig.basis or FRESHNESS_BASIS_V1,
        source_session_ids=sessions,
        source_replay_hash=sig.source_replay_hash,
        computed_at=now,
    )


def competency_state_from_snapshot(snapshot: SkillFreshnessSnapshot) -> CompetencyState:
    return CompetencyState(
        learner_pseudo_id=snapshot.learner_pseudo_id,
        competency_id=snapshot.competency_id,
        ability=snapshot.ability,
        confidence=snapshot.confidence,
        freshness=snapshot.freshness,
        evidence_age_days=snapshot.evidence_age_days,
        reassessment_due=snapshot.reassessment_due,
        basis=snapshot.basis,
        last_session_id=snapshot.source_session_ids[-1],
        source_session_ids=snapshot.source_session_ids,
        updated_at=snapshot.computed_at,
    )
