"""SAMVAAD.d Phase 1 time-on-task recording and request-time rolling totals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_SESSIONS: list["TimeOnTaskRecord"] = []


@dataclass(frozen=True)
class TimeOnTaskRecord:
    session_id: str
    started_at_utc: datetime
    ended_at_utc: datetime
    duration_seconds: float


def clear_time_on_task() -> None:
    _SESSIONS.clear()


def time_on_task_records() -> list[TimeOnTaskRecord]:
    return list(_SESSIONS)


def record_session_time(
    session_id: str,
    *,
    started_at_utc: datetime,
    ended_at_utc: datetime,
) -> TimeOnTaskRecord:
    if started_at_utc.tzinfo is None or ended_at_utc.tzinfo is None:
        raise ValueError("time-on-task timestamps must be timezone-aware")
    if ended_at_utc < started_at_utc:
        raise ValueError("ended_at_utc must not precede started_at_utc")
    record = TimeOnTaskRecord(
        session_id=session_id,
        started_at_utc=started_at_utc.astimezone(timezone.utc),
        ended_at_utc=ended_at_utc.astimezone(timezone.utc),
        duration_seconds=(ended_at_utc - started_at_utc).total_seconds(),
    )
    _SESSIONS.append(record)
    return record


def rolling_time_on_task_seconds(
    *,
    as_of_utc: datetime,
    window_days: int = 30,
) -> float:
    """Compute on read; records exactly at the expired lower edge are dropped."""
    if as_of_utc.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    as_of = as_of_utc.astimezone(timezone.utc)
    cutoff = as_of - timedelta(days=window_days)
    return sum(
        record.duration_seconds
        for record in _SESSIONS
        if cutoff < record.ended_at_utc <= as_of
    )
