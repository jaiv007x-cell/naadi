"""
Ledger read gateway — AuthContext → ConsentResolver → AuditingLedgerReadService.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from services.pratibimb.ledger.unit_mapping import UnknownUnitError
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.audit_decorator import AuditingLedgerReadService
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.errors import InsufficientCohortError, ScopeDeniedError
from services.pratibimb.ledger_read.privacy import PrivacyPolicy, raise_privacy_http
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.ledger_read import (
    CohortSummaryReport,
    EvidenceEventView,
    LearnerEvidenceFilter,
    SessionSummaryView,
    UnitSafetyReport,
)


class LedgerReadGateway:
    def __init__(
        self,
        service: AuditingLedgerReadService,
        consent: ConsentResolver,
        privacy: PrivacyPolicy | None = None,
    ) -> None:
        self.service = service
        self.consent = consent
        self.privacy = privacy or PrivacyPolicy(k_anon_floor=LedgerReadService.K_ANON_FLOOR)

    async def list_sessions(
        self,
        auth: AuthContext,
        *,
        cohort_id: Optional[str] = None,
        learner_pseudo_id: Optional[str] = None,
        case_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[SessionSummaryView]:
        if learner_pseudo_id:
            scope = await self.consent.for_learner(auth, learner_pseudo_id)
            views = await self.service.get_learner_evidence(
                LearnerEvidenceFilter(
                    learner_pseudo_id=learner_pseudo_id,
                    case_id=case_id,
                    since=since,
                    until=until,
                ),
                scope,
                auth=auth,
            )
            return [
                SessionSummaryView(
                    session_id=v.session_id,
                    case_id=v.case_id,
                    cohort_id=v.cohort_id,
                    grade_total_normalized=v.grade_total_normalized,
                    grade_passed=v.grade_passed,
                    recorded_at=v.recorded_at,
                    content_hash=v.content_hash,
                    learner_pseudo_id=v.learner_pseudo_id,
                )
                for v in views[:limit]
            ]

        if cohort_id:
            scope = await self.consent.for_aggregate(auth, resource_id=f"cohort:{cohort_id}")
            try:
                return await self.service.list_cohort_sessions(
                    cohort_id=cohort_id,
                    scope=scope,
                    auth=auth,
                    case_id=case_id,
                    since=since,
                    until=until,
                    limit=limit,
                )
            except InsufficientCohortError:
                raise_privacy_http(403, self.privacy.insufficient_cohort_response())

        raise ValueError("cohort_id or learner_pseudo_id required")

    async def learner_evidence(
        self, auth: AuthContext, filt: LearnerEvidenceFilter
    ) -> list[EvidenceEventView]:
        scope = await self.consent.for_learner(auth, filt.learner_pseudo_id)
        return await self.service.get_learner_evidence(filt, scope, auth=auth)

    async def cohort_summary(
        self, auth: AuthContext, cohort_id: str, *, cause_prefix: Optional[str] = None
    ) -> CohortSummaryReport:
        scope = await self.consent.for_aggregate(auth, resource_id=f"cohort:{cohort_id}")
        try:
            return await self.service.get_cohort_summary(
                cohort_id, scope=scope, auth=auth, cause_prefix=cause_prefix
            )
        except InsufficientCohortError:
            raise_privacy_http(403, self.privacy.insufficient_cohort_response())

    async def unit_safety(
        self, auth: AuthContext, unit_id: str, *, window_days: int = 30
    ) -> UnitSafetyReport:
        scope = await self.consent.for_aggregate(auth, resource_id=f"unit:{unit_id}")
        try:
            return await self.service.get_unit_safety_report(
                auth.tenant_id,
                unit_id,
                window_days=window_days,
                scope=scope,
                auth=auth,
            )
        except InsufficientCohortError:
            raise_privacy_http(403, self.privacy.insufficient_cohort_response())
        except UnknownUnitError:
            raise_privacy_http(404, self.privacy.unknown_unit_response())
