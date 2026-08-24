"""JWT-authenticated gateway over AuditingLedgerReadService."""
from __future__ import annotations

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger.unit_mapping import UnknownUnitError
from services.pratibimb.ledger_read.audit_decorator import AuditingLedgerReadService
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.errors import InsufficientCohortError, ScopeDeniedError
from services.pratibimb.ledger_read.privacy import PrivacyPolicy, raise_privacy_http
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.ledger_read import (
    AggregatePatternReport,
    CohortFilter,
    CohortSummaryReport,
    EvidenceEventView,
    LearnerEvidenceFilter,
    LearnerEvidenceResponse,
    QueryFilters,
    UnitSafetyReport,
)


class JwtLedgerGateway:
    def __init__(
        self,
        service: AuditingLedgerReadService,
        consent: ConsentResolver,
        privacy: PrivacyPolicy | None = None,
    ) -> None:
        self.service = service
        self.consent = consent
        self.privacy = privacy or PrivacyPolicy(k_anon_floor=LedgerReadService.K_ANON_FLOOR)

    async def learner_evidence(
        self,
        auth: AuthContext,
        filt: LearnerEvidenceFilter,
        *,
        request_id: str | None = None,
    ) -> LearnerEvidenceResponse:
        try:
            scope = await self.consent.for_learner(auth, filt.learner_pseudo_id)
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth, "learner_evidence", filt, request_id=request_id
            )
            raise
        events = await self.service.get_learner_evidence(
            filt, scope, auth=auth, request_id=request_id
        )
        return LearnerEvidenceResponse(
            learner_pseudo_id=filt.learner_pseudo_id,
            events=events,
            total=len(events),
        )

    async def unit_safety_report(
        self,
        auth: AuthContext,
        filt: CohortFilter,
        *,
        request_id: str | None = None,
    ) -> UnitSafetyReport:
        if not filt.unit_id:
            raise ValueError("unit_id required")
        try:
            scope = await self.consent.for_aggregate(
                auth, resource_id=f"unit:{filt.unit_id}"
            )
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth, "unit_safety_report", filt, request_id=request_id
            )
            raise
        try:
            return await self.service.get_unit_safety_report(
                auth.tenant_id,
                filt.unit_id,
                window_days=filt.window_days,
                scope=scope,
                auth=auth,
                request_id=request_id,
            )
        except InsufficientCohortError:
            raise_privacy_http(403, self.privacy.insufficient_cohort_response())
        except UnknownUnitError:
            raise_privacy_http(404, self.privacy.unknown_unit_response())

    async def aggregate_patterns(
        self,
        auth: AuthContext,
        filt: CohortFilter,
        *,
        request_id: str | None = None,
    ) -> AggregatePatternReport:
        resource = f"cohort:{filt.cohort_id}" if filt.cohort_id else "aggregate"
        try:
            scope = await self.consent.for_aggregate(auth, resource_id=resource)
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth, "aggregate_patterns", filt, request_id=request_id
            )
            raise
        try:
            return await self.service.get_aggregate_error_patterns(
                QueryFilters(
                    cohort_id=filt.cohort_id,
                    unit_id=filt.unit_id,
                    cause_prefix=filt.cause_prefix,
                ),
                scope,
                auth=auth,
                request_id=request_id,
            )
        except InsufficientCohortError:
            raise_privacy_http(403, self.privacy.insufficient_cohort_response())

    async def cohort_summary(
        self,
        auth: AuthContext,
        cohort_id: str,
        *,
        cause_prefix: str | None = None,
        request_id: str | None = None,
    ) -> CohortSummaryReport:
        try:
            scope = await self.consent.for_aggregate(
                auth, resource_id=f"cohort:{cohort_id}"
            )
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth,
                "cohort_summary",
                {"cohort_id": cohort_id},
                request_id=request_id,
            )
            raise
        try:
            return await self.service.get_cohort_summary(
                cohort_id,
                scope=scope,
                auth=auth,
                cause_prefix=cause_prefix,
                request_id=request_id,
            )
        except InsufficientCohortError:
            raise_privacy_http(403, self.privacy.insufficient_cohort_response())

    async def list_evidence_events(
        self,
        auth: AuthContext,
        filt: LearnerEvidenceFilter,
        *,
        request_id: str | None = None,
    ) -> list[EvidenceEventView]:
        try:
            scope = await self.consent.for_learner(auth, filt.learner_pseudo_id)
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth, "learner_evidence", filt, request_id=request_id
            )
            raise
        return await self.service.get_learner_evidence(
            filt, scope, auth=auth, request_id=request_id
        )

    async def list_cohort_sessions(
        self,
        auth: AuthContext,
        *,
        cohort_id: str,
        case_id: str | None = None,
        since=None,
        until=None,
        limit: int = 100,
        request_id: str | None = None,
    ):
        try:
            scope = await self.consent.for_aggregate(
                auth, resource_id=f"cohort:{cohort_id}"
            )
        except ScopeDeniedError:
            self.service.record_scope_denial(
                auth,
                "list_cohort_sessions",
                {"cohort_id": cohort_id},
                request_id=request_id,
            )
            raise
        try:
            return await self.service.list_cohort_sessions(
                cohort_id=cohort_id,
                scope=scope,
                auth=auth,
                case_id=case_id,
                since=since,
                until=until,
                limit=limit,
                request_id=request_id,
            )
        except InsufficientCohortError:
            raise_privacy_http(403, self.privacy.insufficient_cohort_response())
