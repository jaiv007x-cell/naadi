from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.pratibimb.audit.sink import InMemoryAuditSink
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.audit_decorator import AuditingLedgerReadService
from services.pratibimb.ledger_read.errors import InsufficientCohortError
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.ledger_read import ConsentScope, LearnerEvidenceFilter

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 7, 0, tzinfo=UTC)


@pytest.fixture
def ctx() -> AuthContext:
    return AuthContext(
        subject_pseudo_id="learner-42",
        tenant_id="tenant-a",
        jti="j1",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        source="jwt",
    )


@pytest.fixture
def sink() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def inner():
    m = MagicMock(spec=LedgerReadService)
    m.K_ANON_FLOOR = 50
    m.get_learner_evidence = AsyncMock(
        return_value=[
            {"session_id": "s1", "score": 0.82},
            {"session_id": "s2", "score": 0.74},
        ]
    )
    return m


@pytest.fixture
def denying_inner():
    m = MagicMock(spec=LedgerReadService)
    m.K_ANON_FLOOR = 50
    m.get_learner_evidence = AsyncMock(
        side_effect=InsufficientCohortError(minimum_required=50)
    )
    return m


@pytest.fixture
def service(inner, sink):
    return AuditingLedgerReadService(
        inner,
        sink,
        request_id_provider=lambda: "req-1",
        now=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_success_row_has_fingerprint(service, sink, ctx):
    filt = LearnerEvidenceFilter(learner_pseudo_id="p-1")
    await service.get_learner_evidence(
        filt, ConsentScope.PRECEPTOR_REVIEW, auth=ctx
    )

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome == "ok"
    assert event.result_fingerprint is not None
    assert len(event.result_fingerprint) == 64


@pytest.mark.asyncio
async def test_denial_row_has_null_fingerprint(denying_inner, sink, ctx):
    svc = AuditingLedgerReadService(
        denying_inner,
        sink,
        request_id_provider=lambda: "req-1",
        now=lambda: NOW,
    )
    filt = LearnerEvidenceFilter(learner_pseudo_id="p-1")
    with pytest.raises(InsufficientCohortError):
        await svc.get_learner_evidence(
            filt, ConsentScope.PRECEPTOR_REVIEW, auth=ctx
        )

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome == "insufficient_cohort"
    assert event.result_fingerprint is None


@pytest.mark.asyncio
async def test_fingerprint_stable_across_repeat_calls(service, sink, ctx):
    filt = LearnerEvidenceFilter(learner_pseudo_id="p-1")
    await service.get_learner_evidence(
        filt, ConsentScope.PRECEPTOR_REVIEW, auth=ctx
    )
    await service.get_learner_evidence(
        filt, ConsentScope.PRECEPTOR_REVIEW, auth=ctx
    )
    assert sink.events[0].result_fingerprint == sink.events[1].result_fingerprint


def test_wrapper_refuses_to_construct_with_unregistered_kind(inner, sink):
    class _Bad(AuditingLedgerReadService):
        _REGISTERED_KINDS = frozenset({"learner_evidence", "bogus_kind"})

    with pytest.raises(RuntimeError, match="unregistered"):
        _Bad(inner, sink)
