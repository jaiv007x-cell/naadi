"""
LedgerReadAPI: the only sanctioned entry point for external consumers.
Wraps SqlLedgerReader with k-anonymity floors and optional DP noise.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Protocol

from services.beema.ledger.interface import LedgerQuery, SessionLedgerRecord
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger_read.errors import InsufficientCohortError
from shared.schemas.ledger_read import (
    AggregatePatternReport,
    CohortSummaryReport,
    ConsentScope,
    EvidenceEventView,
    LearnerEvidenceFilter,
    QueryFilters,
    SessionSummaryView,
    SkillDecayCurve,
    UnitSafetyReport,
)


class LedgerReadAPI(Protocol):
    async def get_learner_evidence(
        self, filt: LearnerEvidenceFilter, scope: ConsentScope,
    ) -> list[EvidenceEventView]: ...

    async def get_unit_safety_report(
        self,
        tenant_id: str,
        unit_id: str,
        window_days: int,
        scope: ConsentScope,
        dp_noise_epsilon: float | None = None,
    ) -> UnitSafetyReport: ...

    async def get_skill_decay_curve(
        self, competency_id: str, role: str, cohort_size_min: int = 50,
    ) -> SkillDecayCurve: ...

    async def get_aggregate_error_patterns(
        self,
        filters: QueryFilters,
        scope: ConsentScope,
        dp_noise_epsilon: float | None = None,
    ) -> AggregatePatternReport: ...


class LedgerReadService:
    K_ANON_FLOOR = 50

    def __init__(
        self,
        reader: SqlLedgerReader,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.reader = reader
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def get_learner_evidence(
        self,
        filt: LearnerEvidenceFilter,
        scope: ConsentScope,
    ) -> list[EvidenceEventView]:
        if scope not in {ConsentScope.SELF_LEARNER, ConsentScope.PRECEPTOR_REVIEW}:
            raise PermissionError(f"scope {scope} cannot access individual evidence")
        q = LedgerQuery(
            learner_pseudo_id=filt.learner_pseudo_id,
            case_id=filt.case_id,
            finalized_after_utc=filt.since.isoformat() if filt.since else None,
            finalized_before_utc=filt.until.isoformat() if filt.until else None,
            limit=1000,
        )
        rows = [rec for rec in self.reader.iter_query(q)]
        views = [self._row_to_view(r, filt.cause_prefix) for r in rows]
        return [v for v in views if v is not None]

    async def get_unit_safety_report(
        self,
        tenant_id: str,
        unit_id: str,
        window_days: int,
        scope: ConsentScope,
        dp_noise_epsilon: float | None = None,
    ) -> UnitSafetyReport:
        if scope not in {
            ConsentScope.AGGREGATE_ANALYTICS,
            ConsentScope.RESEARCH_DEIDENTIFIED,
        }:
            raise PermissionError(f"scope {scope} cannot access unit safety report")
        cohort_ids = self.reader.cohort_ids_for_unit(tenant_id, unit_id)
        since = datetime.now(timezone.utc) - timedelta(days=max(1, window_days))
        q = LedgerQuery(
            cohort_id_in=cohort_ids,
            finalized_after_utc=since.isoformat(),
            limit=5000,
        )
        rows = list(self.reader.iter_query(q))
        if len(rows) < self.K_ANON_FLOOR:
            raise InsufficientCohortError(minimum_required=self.K_ANON_FLOOR)
        sessions_with_critical = 0
        cause_sessions: dict[str, set[str]] = {}
        cause_occurrences: dict[str, int] = {}
        for rec in rows:
            had_critical_miss = any(
                ev.critical and not ev.matched for ev in rec.evidence
            )
            if had_critical_miss:
                sessions_with_critical += 1
            for flag in rec.flags:
                cause = flag.cause.value if hasattr(flag.cause, "value") else str(flag.cause)
                cause_occurrences[cause] = cause_occurrences.get(cause, 0) + 1
                cause_sessions.setdefault(cause, set()).add(rec.session_id)
        top_causes = [
            {
                "cause": cause,
                "session_count": len(cause_sessions[cause]),
                "occurrence_count": cause_occurrences[cause],
            }
            for cause in sorted(
                cause_occurrences,
                key=lambda c: (-len(cause_sessions[c]), -cause_occurrences[c], c),
            )[:10]
        ]
        if dp_noise_epsilon is not None:
            top_causes = _apply_laplace_noise(top_causes, dp_noise_epsilon)
        return UnitSafetyReport(
            unit_id=unit_id,
            window_days=window_days,
            cohort_size=len(rows),
            dp_noise_epsilon=dp_noise_epsilon,
            critical_violation_rate=round(
                sessions_with_critical / max(1, len(rows)), 4
            ),
            top_causes=top_causes,
            generated_at=datetime.now(timezone.utc),
        )

    async def get_skill_decay_curve(
        self, competency_id: str, role: str, cohort_size_min: int = 50,
    ) -> SkillDecayCurve:
        raise NotImplementedError("skill decay curves require aggregate projection (PR-2)")

    async def get_aggregate_error_patterns(
        self,
        filters: QueryFilters,
        scope: ConsentScope,
        dp_noise_epsilon: float | None = None,
    ) -> AggregatePatternReport:
        if scope not in {
            ConsentScope.AGGREGATE_ANALYTICS,
            ConsentScope.RESEARCH_DEIDENTIFIED,
        }:
            raise PermissionError(
                f"scope {scope} cannot access aggregate error patterns"
            )
        q = LedgerQuery(
            cohort_id=filters.cohort_id,
            case_id=filters.case_id,
            finalized_after_utc=filters.since.isoformat() if filters.since else None,
            finalized_before_utc=filters.until.isoformat() if filters.until else None,
            limit=5000,
        )
        rows = list(self.reader.iter_query(q))
        if len(rows) < self.K_ANON_FLOOR:
            raise InsufficientCohortError(minimum_required=self.K_ANON_FLOOR)
        cause_sessions: dict[str, set[str]] = {}
        cause_occurrences: dict[str, int] = {}
        for rec in rows:
            for flag in rec.flags:
                cause = flag.cause.value if hasattr(flag.cause, "value") else str(flag.cause)
                if filters.cause_prefix and not cause.startswith(filters.cause_prefix):
                    continue
                cause_occurrences[cause] = cause_occurrences.get(cause, 0) + 1
                cause_sessions.setdefault(cause, set()).add(rec.session_id)
        patterns = [
            {
                "cause": cause,
                "session_count": len(cause_sessions[cause]),
                "occurrence_count": cause_occurrences[cause],
                "pct": round(len(cause_sessions[cause]) / max(1, len(rows)), 4),
                "exemplar_case_ids": [],
            }
            for cause in sorted(
                cause_occurrences,
                key=lambda c: (-len(cause_sessions[c]), -cause_occurrences[c], c),
            )
        ]
        if dp_noise_epsilon is not None:
            patterns = _apply_laplace_noise(patterns, dp_noise_epsilon)
        return AggregatePatternReport(
            filters=filters,
            cohort_size=len(rows),
            dp_noise_epsilon=dp_noise_epsilon,
            patterns=patterns,
            generated_at=datetime.now(timezone.utc),
        )

    async def get_cohort_summary(
        self,
        cohort_id: str,
        *,
        scope: ConsentScope,
        cause_prefix: str | None = None,
    ) -> CohortSummaryReport:
        report = await self.get_aggregate_error_patterns(
            QueryFilters(cohort_id=cohort_id, cause_prefix=cause_prefix),
            scope,
        )
        sessions_with_critical = 0
        q = LedgerQuery(cohort_id=cohort_id, limit=5000)
        rows = list(self.reader.iter_query(q))
        for rec in rows:
            if any(ev.critical and not ev.matched for ev in rec.evidence):
                sessions_with_critical += 1
        return CohortSummaryReport(
            cohort_id=cohort_id,
            cohort_size=report.cohort_size,
            critical_violation_rate=round(
                sessions_with_critical / max(1, len(rows)), 4
            ),
            patterns=report.patterns,
            generated_at=report.generated_at,
        )

    async def list_cohort_sessions(
        self,
        *,
        cohort_id: str,
        scope: ConsentScope,
        case_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[SessionSummaryView]:
        if scope not in {
            ConsentScope.AGGREGATE_ANALYTICS,
            ConsentScope.RESEARCH_DEIDENTIFIED,
        }:
            raise PermissionError(f"scope {scope} cannot list cohort sessions")
        q = LedgerQuery(
            cohort_id=cohort_id,
            case_id=case_id,
            finalized_after_utc=since.isoformat() if since else None,
            finalized_before_utc=until.isoformat() if until else None,
            limit=5000,
        )
        rows = list(self.reader.iter_query(q))
        if len(rows) < self.K_ANON_FLOOR:
            raise InsufficientCohortError(minimum_required=self.K_ANON_FLOOR)
        redact_learner = scope is ConsentScope.RESEARCH_DEIDENTIFIED
        out: list[SessionSummaryView] = []
        for rec in rows[:limit]:
            out.append(
                SessionSummaryView(
                    session_id=rec.session_id,
                    case_id=rec.case_id,
                    cohort_id=rec.cohort_id,
                    grade_total_normalized=rec.grade_total_normalized,
                    grade_passed=rec.grade_passed,
                    recorded_at=datetime.fromisoformat(rec.finalized_at_utc),
                    content_hash=rec.replay_hash,
                    learner_pseudo_id=None if redact_learner else rec.learner_pseudo_id,
                )
            )
        return out

    def _row_to_view(
        self, rec: SessionLedgerRecord, cause_prefix: str | None
    ) -> EvidenceEventView | None:
        flags = [
            {
                "flag": f.flag,
                "cause": f.cause.value if hasattr(f.cause, "value") else str(f.cause),
                "namespace": (
                    f.cause.value.split(".", 1)[0]
                    if hasattr(f.cause, "value")
                    else str(f.cause).split(".", 1)[0]
                ),
                "set_at_s": f.set_at_s,
                "cleared_at_s": f.cleared_at_s,
            }
            for f in rec.flags
        ]
        if cause_prefix and not any(fl["cause"].startswith(cause_prefix) for fl in flags):
            return None
        return EvidenceEventView(
            evidence_id=rec.session_id,
            session_id=rec.session_id,
            learner_pseudo_id=rec.learner_pseudo_id,
            cohort_id=rec.cohort_id,
            case_id=rec.case_id,
            physio_engine_version=rec.physio_engine_version,
            grade_total_normalized=rec.grade_total_normalized,
            grade_passed=rec.grade_passed,
            recorded_at=datetime.fromisoformat(rec.finalized_at_utc),
            content_hash=rec.replay_hash,
            blueprint_content_hash=rec.blueprint_content_hash,
            blueprint_source=rec.blueprint_source,
            flags=flags,
            evidence=[
                {
                    "hit_id": e.hit_id,
                    "axis": e.axis,
                    "matcher": e.matcher,
                    "matched": e.matched,
                    "critical": e.critical,
                }
                for e in rec.evidence
            ],
            superseded_by=None,
        )


def _apply_laplace_noise(patterns: list[dict], epsilon: float) -> list[dict]:
    """Deterministic-ish DP noise for tests; production should use seeded crypto RNG."""
    import random

    if epsilon <= 0:
        return patterns
    scale = 1.0 / epsilon
    rng = random.Random(0)
    noised: list[dict] = []
    for p in patterns:
        row = dict(p)
        for key in ("session_count", "occurrence_count"):
            if key in row:
                row[key] = max(0, int(round(row[key] + rng.expovariate(1 / scale))))
        noised.append(row)
    return noised
