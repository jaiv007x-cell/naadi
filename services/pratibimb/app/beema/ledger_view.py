"""
BEEMA Ledger View — read-only, versioned interface.

BEEMA reads from the Evidence Ledger. It never writes judgments upstream.
This protocol enforces that separation in the type system, not code review.

Any implementation (in-memory for tests, Redis-backed for prod) must
satisfy this protocol. If a method mutates ledger state, it does not
belong here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from services.pratibimb.app.eval.error_dna import ErrorDNA


LEDGER_VIEW_VERSION = "1.0.0"


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    case_id: str
    case_version: str
    completed_at: datetime
    overall_score: float
    letter_grade: str
    error_dna: ErrorDNA | None
    physio_version: str
    grader_version: str
    assessment_mode: str  # "practice", "formative", "summative"


@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class UnitSafetyProfile:
    unit_id: str
    period: DateRange
    clinician_count: int
    session_count: int
    mean_overall: float
    mean_error_dna: dict[str, float]  # dim -> mean across clinicians
    dimensions_below_threshold: list[str]


@runtime_checkable
class BEEMALedgerView(Protocol):
    """
    Read-only interface for BEEMA over the Evidence Ledger.

    Implementations MUST NOT expose any method that mutates ledger state.
    The version field ensures consumers can detect schema evolution.
    """

    @property
    def version(self) -> str: ...

    def sessions_for(
        self,
        clinician_id: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[SessionSummary]: ...

    def ppv_history(
        self,
        clinician_id: str,
        last_n: int = 10,
    ) -> list[ErrorDNA]: ...

    def unit_aggregate(
        self,
        unit_id: str,
        period: DateRange,
    ) -> UnitSafetyProfile: ...


class InMemoryLedgerView:
    """
    In-memory implementation for tests. Production would back this
    with Redis/Postgres reads.
    """

    def __init__(self) -> None:
        self._sessions: list[SessionSummary] = []

    @property
    def version(self) -> str:
        return LEDGER_VIEW_VERSION

    def ingest(self, summary: SessionSummary) -> None:
        """Test helper — not part of the Protocol."""
        self._sessions.append(summary)

    def sessions_for(
        self,
        clinician_id: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[SessionSummary]:
        out = [
            s for s in self._sessions
            if (since is None or s.completed_at >= since)
        ]
        out.sort(key=lambda s: s.completed_at, reverse=True)
        return out[:limit]

    def ppv_history(
        self,
        clinician_id: str,
        last_n: int = 10,
    ) -> list[ErrorDNA]:
        sessions = self.sessions_for(clinician_id, limit=last_n)
        return [s.error_dna for s in sessions if s.error_dna is not None]

    def unit_aggregate(
        self,
        unit_id: str,
        period: DateRange,
    ) -> UnitSafetyProfile:
        in_range = [
            s for s in self._sessions
            if period.start <= s.completed_at <= period.end
        ]
        if not in_range:
            return UnitSafetyProfile(
                unit_id=unit_id, period=period,
                clinician_count=0, session_count=0,
                mean_overall=0.0, mean_error_dna={}, dimensions_below_threshold=[],
            )

        clinician_ids = {s.session_id.split("-")[0] for s in in_range}
        mean_overall = sum(s.overall_score for s in in_range) / len(in_range)

        dna_sessions = [s for s in in_range if s.error_dna is not None]
        mean_dna: dict[str, float] = {}
        if dna_sessions:
            dims = list(dna_sessions[0].error_dna.to_dict().keys())
            for dim in dims:
                vals = [s.error_dna.to_dict()[dim] for s in dna_sessions if s.error_dna]
                mean_dna[dim] = sum(vals) / len(vals) if vals else 0.0

        below = [d for d, v in mean_dna.items() if v < 0.60]

        return UnitSafetyProfile(
            unit_id=unit_id,
            period=period,
            clinician_count=len(clinician_ids),
            session_count=len(in_range),
            mean_overall=mean_overall,
            mean_error_dna=mean_dna,
            dimensions_below_threshold=below,
        )
