from __future__ import annotations

import base64
from typing import Iterator, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from services.beema.ledger.interface import (
    ActionSummary,
    ConfirmationState,
    FlagEpisode,
    LedgerPage,
    LedgerQuery,
    LedgerReader,
    SessionLedgerRecord,
    TypedEvidence,
)
from shared.schemas.flag_causes import FlagCause, FlagClearReason
from services.pratibimb.ledger.models import SessionLedgerRow, TrustedPhysioVersion
from services.pratibimb.ledger.unit_mapping import UnitCohortResolver


def _decode_cursor(c: Optional[str]) -> Optional[str]:
    if not c:
        return None
    return base64.urlsafe_b64decode(c.encode()).decode()


def _encode_cursor(session_id: str) -> str:
    return base64.urlsafe_b64encode(session_id.encode()).decode()


def _parse_confirmation(v: str) -> ConfirmationState:
    try:
        return ConfirmationState(v)
    except ValueError:
        return ConfirmationState.UNCONFIRMED


def _row_to_record(r: SessionLedgerRow) -> SessionLedgerRecord:
    ev = tuple(
        TypedEvidence(
            hit_id=e["hit_id"],
            axis=e["axis"],
            matcher=e["matcher"],
            matched=bool(e["matched"]),
            awarded=float(e["awarded"]),
            weight=float(e["weight"]),
            at_sim_time_s=e.get("at_sim_time_s"),
            critical=bool(e.get("critical", False)),
            negative=bool(e.get("negative", False)),
        )
        for e in (r.evidence or [])
    )

    fl = []
    for f in (r.flags or []):
        cause_raw = f["cause"]
        try:
            cause: FlagCause | str = FlagCause(cause_raw)
        except Exception:
            cause = str(cause_raw)

        clear_reason = None
        if f.get("clear_reason") is not None:
            try:
                clear_reason = FlagClearReason(f["clear_reason"])
            except Exception:
                clear_reason = None

        fl.append(
            FlagEpisode(
                flag=f["flag"],
                cause=cause,
                set_at_s=float(f["set_at_s"]),
                cleared_at_s=f.get("cleared_at_s"),
                clear_reason=clear_reason,
                caused_by_action_id=f.get("caused_by_action_id"),
            )
        )

    ax = dict(r.axis_normalized or {})

    actions = tuple(
        ActionSummary(
            action_id=a["action_id"],
            kind=a["kind"],
            at_sim_time_s=float(a["at_sim_time_s"]),
            canonical_key=a["canonical_key"],
            within_expected_window=a.get("within_expected_window"),
        )
        for a in (r.actions or [])
    )

    return SessionLedgerRecord(
        session_id=r.session_id,
        learner_pseudo_id=r.learner_pseudo_id,
        cohort_id=r.cohort_id,
        case_id=r.case_id,
        case_version=r.case_version,
        physio_engine_version=r.physio_engine_version,
        rubric_version=r.rubric_version,
        replay_hash=r.replay_hash,
        finalized_at_utc=r.finalized_at_utc.isoformat(),
        confirmation=_parse_confirmation(r.confirmation),
        preceptor_pseudo_id=r.preceptor_pseudo_id,
        grade_total_normalized=float(r.grade_total),
        grade_passed=bool(r.grade_passed),
        axis_normalized=ax,
        evidence=ev,
        flags=tuple(fl),
        actions=actions,
        outcome_link_token=r.outcome_link_token,
        blueprint_content_hash=r.blueprint_content_hash,
        blueprint_source=r.blueprint_source,
    )


class SqlLedgerReader(LedgerReader):
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, session_id: str) -> Optional[SessionLedgerRecord]:
        row = self._s.get(SessionLedgerRow, session_id)
        return _row_to_record(row) if row else None

    def query(self, q: LedgerQuery) -> LedgerPage:
        stmt = select(SessionLedgerRow)
        conds = []
        if q.cohort_id:
            conds.append(SessionLedgerRow.cohort_id == q.cohort_id)
        if q.cohort_id_in:
            conds.append(SessionLedgerRow.cohort_id.in_(q.cohort_id_in))
        if q.case_id:
            conds.append(SessionLedgerRow.case_id == q.case_id)
        if q.learner_pseudo_id:
            conds.append(SessionLedgerRow.learner_pseudo_id == q.learner_pseudo_id)
        if q.physio_engine_version_in:
            conds.append(SessionLedgerRow.physio_engine_version.in_(q.physio_engine_version_in))
        if q.rubric_version_in:
            conds.append(SessionLedgerRow.rubric_version.in_(q.rubric_version_in))
        if q.confirmation_in:
            conds.append(
                SessionLedgerRow.confirmation.in_([c.value for c in q.confirmation_in])
            )
        if q.finalized_after_utc:
            stmt = stmt.where(SessionLedgerRow.finalized_at_utc >= q.finalized_after_utc)
        if q.finalized_before_utc:
            stmt = stmt.where(SessionLedgerRow.finalized_at_utc < q.finalized_before_utc)

        cursor = _decode_cursor(q.cursor)
        if cursor:
            conds.append(SessionLedgerRow.session_id > cursor)

        if conds:
            stmt = stmt.where(and_(*conds))

        stmt = stmt.order_by(SessionLedgerRow.session_id).limit(q.limit + 1)
        rows = list(self._s.execute(stmt).scalars())

        next_cursor = None
        if len(rows) > q.limit:
            next_cursor = _encode_cursor(rows[q.limit - 1].session_id)
            rows = rows[: q.limit]

        return LedgerPage(
            records=tuple(_row_to_record(r) for r in rows),
            next_cursor=next_cursor,
        )

    def iter_query(self, q: LedgerQuery) -> Iterator[SessionLedgerRecord]:
        cursor = q.cursor
        while True:
            page = self.query(LedgerQuery(**{**q.__dict__, "cursor": cursor}))
            for rec in page.records:
                yield rec
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    def trusted_physio_versions(self) -> frozenset[str]:
        rows = self._s.execute(select(TrustedPhysioVersion.version)).scalars().all()
        return frozenset(rows)

    def cohort_ids_for_unit(self, tenant_id: str, unit_id: str) -> tuple[str, ...]:
        return UnitCohortResolver(self._s).cohort_ids_for_unit(tenant_id, unit_id)

