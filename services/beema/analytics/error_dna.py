from __future__ import annotations

"""
Error-DNA analytics over ledger reads.

Clusters causal violation patterns from flag episodes and critical rubric misses.
This is an analytics projection — it does not rewrite upstream judgments.
"""

from dataclasses import dataclass, field
from typing import Optional

from services.beema.ledger.interface import FlagEpisode, SessionLedgerRecord, TypedEvidence


def _cause_str(cause) -> str:
    return cause.value if hasattr(cause, "value") else str(cause)


def _reason_str(flag: FlagEpisode) -> str:
    if flag.clear_reason is not None:
        return (
            flag.clear_reason.value
            if hasattr(flag.clear_reason, "value")
            else str(flag.clear_reason)
        )
    return "unresolved"


@dataclass
class ViolationCluster:
    """Aggregate count of a causal (cause, reason) or critical-miss pattern."""

    cluster_key: str
    cause: str
    reason: str
    kind: str  # "flag_episode" | "critical_miss"
    session_count: int = 0
    occurrence_count: int = 0
    session_ids: set[str] = field(default_factory=set)
    learner_ids: set[str] = field(default_factory=set)
    hit_ids: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)

    @property
    def learner_count(self) -> int:
        return len(self.learner_ids)


@dataclass
class ErrorDNAUpdater:
    """
    Ingest adapter that clusters critical violations for BEEMA longitudinal views.

    Idempotent on session_id.
    """

    processed_session_ids: list[str] = field(default_factory=list)
    clusters: dict[str, ViolationCluster] = field(default_factory=dict)
    _seen_session_ids: set[str] = field(default_factory=set, repr=False)

    def update_from_session(self, rec: SessionLedgerRecord) -> None:
        if rec.session_id in self._seen_session_ids:
            return
        self._seen_session_ids.add(rec.session_id)
        self.processed_session_ids.append(rec.session_id)

        for flag in rec.flags:
            self._absorb_flag(rec, flag)

        for ev in rec.evidence:
            if ev.critical and not ev.matched:
                self._absorb_critical_miss(rec, ev)

    def _absorb_flag(self, rec: SessionLedgerRecord, flag: FlagEpisode) -> None:
        cause = _cause_str(flag.cause)
        reason = _reason_str(flag)
        key = f"flag:{cause}|{reason}"
        cluster = self.clusters.get(key)
        if cluster is None:
            cluster = ViolationCluster(
                cluster_key=key,
                cause=cause,
                reason=reason,
                kind="flag_episode",
            )
            self.clusters[key] = cluster
        cluster.occurrence_count += 1
        if rec.session_id not in cluster.session_ids:
            cluster.session_ids.add(rec.session_id)
            cluster.session_count += 1
        cluster.learner_ids.add(rec.learner_pseudo_id)
        cluster.flags.add(flag.flag)

    def _absorb_critical_miss(
        self, rec: SessionLedgerRecord, ev: TypedEvidence
    ) -> None:
        key = f"miss:{ev.hit_id}"
        cluster = self.clusters.get(key)
        if cluster is None:
            cluster = ViolationCluster(
                cluster_key=key,
                cause="rubric.critical_miss",
                reason=ev.hit_id,
                kind="critical_miss",
            )
            self.clusters[key] = cluster
        cluster.occurrence_count += 1
        if rec.session_id not in cluster.session_ids:
            cluster.session_ids.add(rec.session_id)
            cluster.session_count += 1
        cluster.learner_ids.add(rec.learner_pseudo_id)
        cluster.hit_ids.add(ev.hit_id)

    def top_clusters(self, *, limit: int = 10) -> tuple[ViolationCluster, ...]:
        """Rank by independent session recurrence, then total occurrences."""
        ranked = sorted(
            self.clusters.values(),
            key=lambda c: (-c.session_count, -c.occurrence_count, c.cluster_key),
        )
        return tuple(ranked[:limit])

    def last_processed(self) -> Optional[str]:
        if not self.processed_session_ids:
            return None
        return self.processed_session_ids[-1]
