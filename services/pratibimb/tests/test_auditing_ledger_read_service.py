"""Tests for AuditingLedgerReadService wrapper."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.audit_decorator import AuditingLedgerReadService
from services.pratibimb.ledger_read.errors import InsufficientCohortError, ScopeDeniedError
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.ledger_read import ConsentScope, LearnerEvidenceFilter, UnitSafetyReport

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 7, 0, tzinfo=UTC)


class RecordingSink(InMemoryAuditSink):
    def __init__(self) -> None:
        super().__init__()
        self.should_fail = False

    def emit(self, evt: AuditEvent) -> None:
        if self.should_fail:
            raise RuntimeError("simulated sink failure")
        super().emit(evt)


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def auth() -> AuthContext:
    return AuthContext(
        subject_pseudo_id="preceptor_7",
        tenant_id="tenant_aiims_delhi",
        jti="jti-1",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        source="jwt",
    )


@pytest.fixture
def inner():
    m = MagicMock(spec=LedgerReadService)
    m.K_ANON_FLOOR = 50
    m.get_learner_evidence = AsyncMock(
        return_value=[
            {"session_id": "s1", "score": 0.9},
            {"session_id": "s2", "score": 0.8},
        ]
    )
    m.get_unit_safety_report = AsyncMock(
        return_value=UnitSafetyReport(
            unit_id="ward-3b",
            window_days=30,
            cohort_size=87,
            dp_noise_epsilon=0.1,
            critical_violation_rate=0.1,
            top_causes=[],
            generated_at=NOW,
        )
    )
    m.get_aggregate_error_patterns = AsyncMock()
    m.get_skill_decay_curve = AsyncMock()
    return m


@pytest.fixture
def fallback(tmp_path) -> JsonlFallbackSink:
    return JsonlFallbackSink(tmp_path)


@pytest.fixture
def service(inner, sink, fallback, auth):
    return AuditingLedgerReadService(
        inner,
        sink,
        fallback_sink=fallback,
        request_id_provider=lambda: "req-xyz-123",
        now=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_learner_evidence_emits_one_success_event(service, sink, auth, inner):
    filt = LearnerEvidenceFilter(learner_pseudo_id="learner-p-42")
    await service.get_learner_evidence(
        filt, ConsentScope.PRECEPTOR_REVIEW, auth=auth
    )
    assert len(sink.events) == 1
    evt = sink.events[0]
    assert evt.query_kind == "learner_evidence"
    assert evt.subject_pseudo_id == "learner-p-42"
    assert evt.scope == ConsentScope.PRECEPTOR_REVIEW.value
    assert evt.outcome == "ok"
    assert evt.result_row_count == 2
    assert evt.error_kind is None
    assert evt.tenant_id == "tenant_aiims_delhi"
    assert evt.caller_kind == "preceptor"
    assert evt.request_id == "req-xyz-123"
    assert evt.result_fingerprint is not None
    assert len(evt.result_fingerprint) == 64
    inner.get_learner_evidence.assert_awaited_once()


@pytest.mark.asyncio
async def test_unit_safety_report_records_k_floor(service, sink, auth):
    await service.get_unit_safety_report(
        "tenant_aiims_delhi",
        "ward-3b",
        30,
        ConsentScope.AGGREGATE_ANALYTICS,
        auth=auth,
    )
    evt = sink.events[0]
    assert evt.k_anonymity_floor_applied == 50
    assert evt.result_row_count == 87
    assert evt.subject_pseudo_id == "ward-3b"


@pytest.mark.asyncio
async def test_k_anon_violation_emits_error_event_and_reraises(service, sink, auth, inner):
    inner.get_unit_safety_report.side_effect = InsufficientCohortError(
        minimum_required=50
    )
    with pytest.raises(InsufficientCohortError):
        await service.get_unit_safety_report(
            "tenant_aiims_delhi",
            "ward-9x",
            7,
            ConsentScope.AGGREGATE_ANALYTICS,
            auth=auth,
        )
    evt = sink.events[0]
    assert evt.outcome == "insufficient_cohort"
    assert evt.error_kind == "k_anonymity_floor"
    assert evt.result_fingerprint is None
    assert evt.result_row_count is None


@pytest.mark.asyncio
async def test_consent_scope_violation_classified(service, sink, auth, inner):
    inner.get_learner_evidence.side_effect = ScopeDeniedError(
        required=frozenset({ConsentScope.AGGREGATE_ANALYTICS}),
        granted=frozenset({ConsentScope.PRECEPTOR_REVIEW}),
    )
    with pytest.raises(ScopeDeniedError):
        await service.get_learner_evidence(
            LearnerEvidenceFilter(learner_pseudo_id="p-1"),
            ConsentScope.PRECEPTOR_REVIEW,
            auth=auth,
        )
    assert sink.events[0].error_kind == "consent_scope"
    assert sink.events[0].outcome == "scope_denied"


@pytest.mark.asyncio
async def test_permission_error_classified_as_consent_scope(service, sink, auth, inner):
    inner.get_learner_evidence.side_effect = PermissionError("scope denied")
    with pytest.raises(PermissionError):
        await service.get_learner_evidence(
            LearnerEvidenceFilter(learner_pseudo_id="p-2"),
            ConsentScope.PRECEPTOR_REVIEW,
            auth=auth,
        )
    assert sink.events[0].error_kind == "consent_scope"


@pytest.mark.asyncio
async def test_unexpected_error_classified_as_unexpected(service, sink, auth, inner):
    inner.get_learner_evidence.side_effect = ValueError("something weird")
    with pytest.raises(ValueError):
        await service.get_learner_evidence(
            LearnerEvidenceFilter(learner_pseudo_id="p-3"),
            ConsentScope.PRECEPTOR_REVIEW,
            auth=auth,
        )
    assert sink.events[0].error_kind == "unexpected"
    assert sink.events[0].outcome == "error"


@pytest.mark.asyncio
async def test_sink_failure_captures_fallback(service, sink, fallback, auth, inner, caplog):
    sink.should_fail = True
    result = await service.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="p-fallback"),
        ConsentScope.PRECEPTOR_REVIEW,
        auth=auth,
    )
    assert len(result) == 2
    assert "audit_sink_emit_failed" in caplog.text
    day = NOW.strftime("%Y-%m-%d")
    rows = fallback.read_lines(auth.tenant_id, day)
    assert len(rows) == 1
    assert rows[0]["fallback_reason"] == "RuntimeError"
    assert rows[0]["query_kind"] == "learner_evidence"


@pytest.mark.asyncio
async def test_sink_failure_does_not_mask_success(service, sink, auth, inner, caplog):
    sink.should_fail = True
    result = await service.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="p-4"),
        ConsentScope.PRECEPTOR_REVIEW,
        auth=auth,
    )
    assert len(result) == 2
    assert "audit_sink_emit_failed" in caplog.text


@pytest.mark.asyncio
async def test_sink_failure_does_not_mask_error(service, sink, auth, inner):
    sink.should_fail = True
    inner.get_learner_evidence.side_effect = InsufficientCohortError(
        minimum_required=50
    )
    with pytest.raises(InsufficientCohortError):
        await service.get_learner_evidence(
            LearnerEvidenceFilter(learner_pseudo_id="p-5"),
            ConsentScope.PRECEPTOR_REVIEW,
            auth=auth,
        )


@pytest.mark.asyncio
async def test_params_hash_is_stable_across_calls(service, sink, auth):
    await service.get_unit_safety_report(
        "tenant_aiims_delhi",
        "ward-3b",
        30,
        ConsentScope.AGGREGATE_ANALYTICS,
        auth=auth,
        dp_noise_epsilon=0.1,
    )
    await service.get_unit_safety_report(
        "tenant_aiims_delhi",
        "ward-3b",
        30,
        ConsentScope.AGGREGATE_ANALYTICS,
        auth=auth,
        dp_noise_epsilon=0.1,
    )
    assert sink.events[0].query_params_hash == sink.events[1].query_params_hash
    assert sink.events[0].query_id != sink.events[1].query_id


@pytest.mark.asyncio
async def test_params_hash_differs_on_different_params(service, sink, auth):
    await service.get_unit_safety_report(
        "tenant_aiims_delhi", "ward-3b", 30, ConsentScope.AGGREGATE_ANALYTICS, auth=auth
    )
    await service.get_unit_safety_report(
        "tenant_aiims_delhi", "ward-3b", 31, ConsentScope.AGGREGATE_ANALYTICS, auth=auth
    )
    assert sink.events[0].query_params_hash != sink.events[1].query_params_hash


@pytest.mark.asyncio
async def test_duration_ms_is_recorded(service, sink, auth):
    await service.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="p-6"),
        ConsentScope.PRECEPTOR_REVIEW,
        auth=auth,
    )
    assert sink.events[0].duration_ms >= 0
    assert sink.events[0].duration_ms < 10_000


@pytest.mark.asyncio
async def test_at_utc_is_timezone_aware(service, sink, auth):
    await service.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="p-7"),
        ConsentScope.PRECEPTOR_REVIEW,
        auth=auth,
    )
    assert sink.events[0].at_utc.tzinfo is not None
