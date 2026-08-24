from __future__ import annotations

"""
Skill Decay v1 (BEEMA projection over ledger reads).

This implementation consumes `SessionLedgerRecord` and projects an exponentially
decaying confidence score per competency into a deterministic in-memory store.

Until the ledger schema carries explicit competency tags, we use the ledger's
`axis_normalized` keys as competency identifiers.

BEEMA contract:
  - deterministic given the same ledger records
  - idempotent on (replay_hash, competency_id)
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from services.beema.ledger.interface import SessionLedgerRecord


DEFAULT_HALF_LIFE_DAYS: dict[str, float] = {
    # Reasonable defaults until competency catalog exists at ledger layer.
    "ACTION": 28.0,
    "TIMING": 45.0,
    "SAFETY": 60.0,
    "COMMUNICATION": 90.0,
    "DIAGNOSTIC": 45.0,
}
FALLBACK_HALF_LIFE_DAYS: float = 45.0
CONFIDENCE_SHRINK_PER_SAMPLE: float = 0.15
DECAY_FLOOR: float = -1.0
DECAY_CEIL: float = 1.0
POLICY_BASIS: str = "policy_prior_v1"
STALE_FRESHNESS_THRESHOLD: float = 0.5


def _parse_iso_datetime(v: str) -> datetime:
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clamp_unit(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _clamp_delta(x: float) -> float:
    return max(DECAY_FLOOR, min(DECAY_CEIL, float(x)))


def _freshness(days_since: float, half_life_days: float) -> float:
    """Policy-prior exponential freshness: evidence for competency is becoming stale."""
    if half_life_days <= 0:
        return 0.0
    return math.exp(-math.log(2.0) * max(0.0, days_since) / half_life_days)


@dataclass(frozen=True)
class SkillDecayKey:
    learner_id: str
    competency_id: str


@dataclass
class SkillDecayEntry:
    key: SkillDecayKey
    last_practice_at_utc: datetime
    last_normalized_score: float
    last_session_id: str
    last_replay_hash: str
    half_life_days: float
    sample_count: int = 1
    source_session_ids: tuple[str, ...] = ()

    def effective_score(self, now_utc: datetime) -> float:
        days = max(0.0, (now_utc - self.last_practice_at_utc).total_seconds() / 86400.0)
        # Half-life decay: score halves every `half_life_days`.
        return self.last_normalized_score * (0.5 ** (days / self.half_life_days))

    def confidence_lower(self, now_utc: datetime) -> float:
        eff = self.effective_score(now_utc)
        n = max(1, self.sample_count)
        # Deterministic shrink factor with sample-count uncertainty.
        shrinkage = 1.0 / math.sqrt(n)
        return max(0.0, eff - CONFIDENCE_SHRINK_PER_SAMPLE * shrinkage)


@dataclass(frozen=True)
class SkillDecaySignal:
    learner_id: str
    competency_id: str
    effective_score: float
    confidence_lower: float
    days_since_practice: float
    stale: bool
    source_session_id: str
    source_replay_hash: str
    # Session-to-session delta semantics (clamped for downstream consumers)
    decay_delta: float = 0.0
    competency_after: float = 0.0
    competency_before: float | None = None
    time_since_last_s: float = 0.0
    freshness: float = 1.0
    evidence_age_days: float = 0.0
    reassessment_due: bool = False
    basis: str = POLICY_BASIS


class SkillDecayStore:
    def __init__(self) -> None:
        self._entries: dict[SkillDecayKey, SkillDecayEntry] = {}
        self._seen: set[tuple[str, str]] = set()  # (replay_hash, competency_id)

    def already_seen(self, replay_hash: str, competency_id: str) -> bool:
        k = (replay_hash, competency_id)
        if k in self._seen:
            return True
        self._seen.add(k)
        return False

    def get(self, key: SkillDecayKey) -> Optional[SkillDecayEntry]:
        return self._entries.get(key)

    def upsert(self, entry: SkillDecayEntry) -> None:
        self._entries[entry.key] = entry


class SkillDecayUpdater:
    """
    Ingest adapter + deterministic Skill Decay v1 projection.

    The consumer calls `update_from_session()`; the updater maintains a
    projection store and optionally emits `SkillDecaySignal` via `on_signal`.
    """

    name = "skill_decay"

    def __init__(
        self,
        store: Optional[SkillDecayStore] = None,
        *,
        half_lives_days: Optional[dict[str, float]] = None,
        on_signal: Optional[Callable[[SkillDecaySignal], None]] = None,
    ) -> None:
        self.store = store or SkillDecayStore()
        self.half_lives_days = half_lives_days or dict(DEFAULT_HALF_LIFE_DAYS)
        self.on_signal = on_signal
        self.signals: list[SkillDecaySignal] = []
        self._processed_session_ids: list[str] = []

    def update_from_session(self, rec: SessionLedgerRecord) -> None:
        now_utc = _parse_iso_datetime(rec.finalized_at_utc)
        last_session_id = rec.session_id

        # Deterministic projection over the axis-normalized snapshot.
        for competency_id, raw_score in rec.axis_normalized.items():
            competency_id = str(competency_id)
            if self.store.already_seen(rec.replay_hash, competency_id):
                continue

            score = _clamp_unit(raw_score)
            key = SkillDecayKey(learner_id=rec.learner_pseudo_id, competency_id=competency_id)
            prev = self.store.get(key)
            half_life = float(self.half_lives_days.get(competency_id, FALLBACK_HALF_LIFE_DAYS))
            competency_before = prev.last_normalized_score if prev is not None else None

            if prev is None:
                entry = SkillDecayEntry(
                    key=key,
                    last_practice_at_utc=now_utc,
                    last_normalized_score=score,
                    last_session_id=last_session_id,
                    last_replay_hash=rec.replay_hash,
                    half_life_days=half_life,
                    sample_count=1,
                    source_session_ids=(last_session_id,),
                )
                time_since_last_s = 0.0
            else:
                sessions = prev.source_session_ids
                if last_session_id not in sessions:
                    sessions = sessions + (last_session_id,)
                if now_utc >= prev.last_practice_at_utc:
                    time_since_last_s = (now_utc - prev.last_practice_at_utc).total_seconds()
                    entry = SkillDecayEntry(
                        key=key,
                        last_practice_at_utc=now_utc,
                        last_normalized_score=score,
                        last_session_id=last_session_id,
                        last_replay_hash=rec.replay_hash,
                        half_life_days=half_life,
                        sample_count=prev.sample_count + 1,
                        source_session_ids=sessions,
                    )
                else:
                    time_since_last_s = 0.0
                    entry = SkillDecayEntry(
                        key=key,
                        last_practice_at_utc=prev.last_practice_at_utc,
                        last_normalized_score=prev.last_normalized_score,
                        last_session_id=prev.last_session_id,
                        last_replay_hash=prev.last_replay_hash,
                        half_life_days=prev.half_life_days,
                        sample_count=prev.sample_count + 1,
                        source_session_ids=sessions,
                    )

            self.store.upsert(entry)
            competency_after = _clamp_unit(entry.last_normalized_score)
            if competency_before is None:
                decay_delta = 0.0
            else:
                decay_delta = _clamp_delta(competency_after - competency_before)

            eff = entry.effective_score(now_utc)
            conf = entry.confidence_lower(now_utc)
            days_since = (now_utc - entry.last_practice_at_utc).total_seconds() / 86400.0
            fresh = _freshness(days_since, half_life)
            stale = fresh < STALE_FRESHNESS_THRESHOLD
            reassessment_due = stale or eff < 0.5 * max(competency_after, 0.01)
            sig = SkillDecaySignal(
                learner_id=entry.key.learner_id,
                competency_id=entry.key.competency_id,
                effective_score=eff,
                confidence_lower=conf,
                days_since_practice=days_since,
                stale=stale,
                source_session_id=rec.session_id,
                source_replay_hash=rec.replay_hash,
                decay_delta=decay_delta,
                competency_after=competency_after,
                competency_before=competency_before,
                time_since_last_s=max(0.0, time_since_last_s),
                freshness=fresh,
                evidence_age_days=days_since,
                reassessment_due=reassessment_due,
                basis=POLICY_BASIS,
            )
            self.signals.append(sig)
            if self.on_signal is not None:
                self.on_signal(sig)

        # Track ingestion progress for tests/operational logging.
        self._processed_session_ids.append(rec.session_id)

    def last_processed(self) -> Optional[str]:
        if not self._processed_session_ids:
            return None
        return self._processed_session_ids[-1]

    def ingest(self, rec: SessionLedgerRecord) -> SkillDecaySignal | None:
        """
        Test-friendly single-session ingest. Returns the last competency signal
        emitted for this record (typically one axis in unit tests).
        """
        before = len(self.signals)
        self.update_from_session(rec)
        if len(self.signals) == before:
            return None
        return self.signals[-1]

