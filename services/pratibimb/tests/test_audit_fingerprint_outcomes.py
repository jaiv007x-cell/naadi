"""Non-ok audit outcomes must never carry a result_fingerprint."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.pratibimb.audit.sink import InMemoryAuditSink
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.audit_decorator import AuditingLedgerReadService
from services.pratibimb.ledger_read.errors import InsufficientCohortError, ScopeDeniedError
from services.pratibimb.ledger_read.query_kinds import LIVE_QUERY_KINDS
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.ledger_read import (
    AggregatePatternReport,
    CohortSummaryReport,
    ConsentScope,
    LearnerEvidenceFilter,
    QueryFilters,
    SessionSummaryView,
    UnitSafetyReport,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 7, 0, tzinfo=UTC)


@pytest.fixture
def auth() -> AuthContext:
    return AuthContext(
        subject_pseudo_id="preceptor_1",
        tenant_id="tenant-a",
        jti="jti-1",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        source="jwt",
    )


@pytest.fixture
def sink() -> InMemoryAuditSink:
    return InMemoryAuditSink()


def _service(inner, sink) -> AuditingLedgerReadService:
    return AuditingLedgerReadService(
        inner,
        sink,
        request_id_provider=lambda: "req-1",
        now=lambda: NOW,
    )


def _ok_unit_report() -> UnitSafetyReport:
    return UnitSafetyReport(
        unit_id="ward-1",
        window_days=30,
        cohort_size=87,
        dp_noise_epsilon=0.1,
        critical_violation_rate=0.1,
        top_causes=[],
        generated_at=NOW,
    )


def _ok_aggregate_report() -> AggregatePatternReport:
    return AggregatePatternReport(
        filters=QueryFilters(cohort_id="cohort-a"),
        patterns=[],
        cohort_size=55,
        generated_at=NOW,
    )


def _ok_cohort_summary() -> CohortSummaryReport:
    return CohortSummaryReport(
        cohort_id="cohort-a",
        cohort_size=55,
        critical_violation_rate=0.1,
        patterns=[],
        generated_at=NOW,
    )


def _ok_sessions() -> list[SessionSummaryView]:
    return [
        SessionSummaryView(
            session_id="s1",
            case_id="c1",
            cohort_id="cohort-a",
            grade_total_normalized=0.8,
            grade_passed=True,
            recorded_at=NOW,
            content_hash="hash-1",
            learner_pseudo_id="L1",
        )
    ]


def _ok_evidence() -> list[dict]:
    return [{"session_id": "s1", "score": 0.82}]


EMITTER_CALLS = {
    "learner_evidence": lambda svc, auth: svc.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="L1"),
        ConsentScope.PRECEPTOR_REVIEW,
        auth=auth,
    ),
    "unit_safety_report": lambda svc, auth: svc.get_unit_safety_report(
        "tenant-a",
        "ward-1",
        30,
        ConsentScope.AGGREGATE_ANALYTICS,
        auth=auth,
    ),
    "aggregate_patterns": lambda svc, auth: svc.get_aggregate_error_patterns(
        QueryFilters(cohort_id="cohort-a"),
        ConsentScope.AGGREGATE_ANALYTICS,
        auth=auth,
    ),
    "cohort_summary": lambda svc, auth: svc.get_cohort_summary(
        "cohort-a",
        scope=ConsentScope.AGGREGATE_ANALYTICS,
        auth=auth,
    ),
    "list_cohort_sessions": lambda svc, auth: svc.list_cohort_sessions(
        cohort_id="cohort-a",
        scope=ConsentScope.AGGREGATE_ANALYTICS,
        auth=auth,
    ),
}


def _inner_for(query_kind: str) -> MagicMock:
    inner = MagicMock(spec=LedgerReadService)
    inner.K_ANON_FLOOR = 50
    inner.get_learner_evidence = AsyncMock(return_value=_ok_evidence())
    inner.get_unit_safety_report = AsyncMock(return_value=_ok_unit_report())
    inner.get_aggregate_error_patterns = AsyncMock(return_value=_ok_aggregate_report())
    inner.get_cohort_summary = AsyncMock(return_value=_ok_cohort_summary())
    inner.list_cohort_sessions = AsyncMock(return_value=_ok_sessions())
    return inner


def _configure_error(inner: MagicMock, query_kind: str, exc: BaseException) -> None:
    if query_kind == "learner_evidence":
        inner.get_learner_evidence.side_effect = exc
    elif query_kind == "unit_safety_report":
        inner.get_unit_safety_report.side_effect = exc
    elif query_kind == "aggregate_patterns":
        inner.get_aggregate_error_patterns.side_effect = exc
    elif query_kind == "cohort_summary":
        inner.get_cohort_summary.side_effect = exc
    elif query_kind == "list_cohort_sessions":
        inner.list_cohort_sessions.side_effect = exc


NON_OK_CASES = [
    pytest.param(
        ScopeDeniedError(
            required=frozenset({ConsentScope.AGGREGATE_ANALYTICS}),
            granted=frozenset({ConsentScope.PRECEPTOR_REVIEW}),
        ),
        "scope_denied",
        id="scope_denied",
    ),
    pytest.param(
        InsufficientCohortError(minimum_required=50),
        "insufficient_cohort",
        id="insufficient_cohort",
    ),
    pytest.param(
        ValueError("unexpected"),
        "error",
        id="error",
    ),
]


@pytest.mark.parametrize("query_kind", sorted(LIVE_QUERY_KINDS))
@pytest.mark.parametrize("exc,outcome", NON_OK_CASES)
@pytest.mark.asyncio
async def test_non_ok_outcomes_have_null_fingerprint(
    query_kind: str,
    exc: BaseException,
    outcome: str,
    auth: AuthContext,
    sink: InMemoryAuditSink,
):
    inner = _inner_for(query_kind)
    _configure_error(inner, query_kind, exc)
    svc = _service(inner, sink)

    with pytest.raises(type(exc)):
        await EMITTER_CALLS[query_kind](svc, auth)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.query_kind == query_kind
    assert event.outcome == outcome
    assert event.result_fingerprint is None


@pytest.mark.asyncio
async def test_scope_denial_via_record_scope_denial_has_null_fingerprint(
    auth: AuthContext,
    sink: InMemoryAuditSink,
):
    inner = MagicMock(spec=LedgerReadService)
    inner.K_ANON_FLOOR = 50
    svc = _service(inner, sink)

    svc.record_scope_denial(
        auth,
        "learner_evidence",
        {"filt": LearnerEvidenceFilter(learner_pseudo_id="L1")},
    )

    event = sink.events[0]
    assert event.outcome == "scope_denied"
    assert event.result_fingerprint is None
