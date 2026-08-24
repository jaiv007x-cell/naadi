"""
Institutional unit → ledger cohort resolution (typed, tenant-scoped, time-bounded).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.pratibimb.ledger.models import CohortUnitAssignmentRow, InstitutionalUnit


class UnknownUnitError(LookupError):
    """Raised when no active cohort assignment exists for the unit."""


class UnitCohortResolver:
    def __init__(self, session: Session) -> None:
        self._s = session

    def cohort_ids_for_unit(
        self,
        tenant_id: str,
        unit_id: str,
        *,
        at: datetime | None = None,
    ) -> tuple[str, ...]:
        at = at or datetime.now(timezone.utc)
        unit = self._s.get(InstitutionalUnit, {"tenant_id": tenant_id, "unit_id": unit_id})
        if unit is None:
            raise UnknownUnitError(f"unknown unit {tenant_id}/{unit_id}")
        if unit.effective_from_utc and at < unit.effective_from_utc:
            raise UnknownUnitError(f"unit {unit_id} not yet effective")
        if unit.effective_to_utc and at >= unit.effective_to_utc:
            raise UnknownUnitError(f"unit {unit_id} no longer effective")

        rows = self._s.execute(
            select(CohortUnitAssignmentRow).where(
                CohortUnitAssignmentRow.tenant_id == tenant_id,
                CohortUnitAssignmentRow.unit_id == unit_id,
            )
        ).scalars().all()
        active: list[str] = []
        for row in rows:
            if row.valid_from_utc and at < row.valid_from_utc:
                continue
            if row.valid_to_utc and at >= row.valid_to_utc:
                continue
            active.append(row.cohort_id)
        if not active:
            raise UnknownUnitError(
                f"no active cohort assignment for unit {tenant_id}/{unit_id!r}"
            )
        return tuple(sorted(set(active)))
