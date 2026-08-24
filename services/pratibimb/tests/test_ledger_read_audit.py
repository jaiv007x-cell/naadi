"""Integration tests for ledger read audit trail."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from services.pratibimb.audit.models_audit import LedgerReadAuditRow
from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink, SqlAuditSink, hash_params
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger.models import Base, SessionLedgerRow, TrustedPhysioVersion
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger_read.audit_decorator import AuditingLedgerReadService
from services.pratibimb.ledger_read.errors import InsufficientCohortError, ScopeDeniedError
from services.pratibimb.ledger_read.factory import build_audited_ledger_read_service
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink
from services.pratibimb.ledger_read.jwt_gateway import JwtLedgerGateway
from services.pratibimb.ledger_read.privacy import PrivacyPolicy
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.ledger_read import ConsentScope, LearnerEvidenceFilter, QueryFilters
import services.pratibimb.audit.models_audit  # noqa: F401

UTC = timezone.utc
TENANT = "tenant-virohan"
SUBJECT = "P1"
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _auth() -> AuthContext:
    return AuthContext(
        subject_pseudo_id=SUBJECT,
        tenant_id=TENANT,
        jti="jti-1",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        source="jwt",
    )


def _mk_row(session_id: str, learner: str = "L1") -> SessionLedgerRow:
    return SessionLedgerRow(
        session_id=session_id,
        learner_pseudo_id=learner,
        cohort_id="cohortA",
        case_id="case1",
        case_version="1",
        physio_engine_version="physio@v1",
        rubric_version="r1",
        replay_hash=f"hash-{session_id}",
        finalized_at_utc=NOW,
        confirmation="confirmed",
        preceptor_pseudo_id=None,
        grade_total=0.8,
        grade_passed=True,
        axis_normalized={},
        evidence=[],
        flags=[],
        actions=[],
        outcome_link_token=None,
    )


class _FailingAuditSink(InMemoryAuditSink):
    def emit(self, event: AuditEvent) -> None:
        raise RuntimeError("simulated primary sink failure")


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def audited_service(audit_sink, tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        for i in range(55):
            s.add(_mk_row(f"s{i}", learner=f"L{i % 5}"))
        s.commit()
        reader = SqlLedgerReader(s)
        svc = build_audited_ledger_read_service(
            reader,
            audit_sink=audit_sink,
            fallback_sink=JsonlFallbackSink(tmp_path),
            request_id_provider=lambda: "req-1",
        )
        svc._now = lambda: NOW  # type: ignore[method-assign]
        yield svc


class _StubConsent:
    def __init__(self, scope: ConsentScope):
        self._scope = scope

    async def for_learner(self, auth, learner_id):
        return self._scope

    async def for_aggregate(self, auth, resource_id):
        return self._scope


class _DenyConsent:
    async def for_learner(self, auth, learner_id):
        raise ScopeDeniedError(
            required=frozenset({ConsentScope.PRECEPTOR_REVIEW}),
            granted=frozenset(),
        )

    async def for_aggregate(self, auth, resource_id):
        raise ScopeDeniedError(
            required=frozenset({ConsentScope.AGGREGATE_ANALYTICS}),
            granted=frozenset(),
        )


def test_hash_params_deterministic():
    filt = LearnerEvidenceFilter(learner_pseudo_id="L1")
    h1, n1 = hash_params(filt)
    h2, n2 = hash_params(filt)
    assert h1 == h2
    assert n1 == n2
    assert len(h1) == 64


@pytest.mark.asyncio
async def test_ok_outcome_records_row_count(audited_service, audit_sink):
    filt = LearnerEvidenceFilter(learner_pseudo_id="L1")
    await audited_service.get_learner_evidence(
        filt, ConsentScope.SELF_LEARNER, auth=_auth()
    )
    event = audit_sink.last()
    assert event.outcome == "ok"
    assert event.result_row_count is not None
    assert event.result_fingerprint is not None
    assert event.tenant_id == TENANT
    assert event.subject_pseudo_id == "L1"


@pytest.mark.asyncio
async def test_scope_denied_via_permission_error(audited_service, audit_sink):
    filt = LearnerEvidenceFilter(learner_pseudo_id="L1")
    with pytest.raises(PermissionError):
        await audited_service.get_learner_evidence(
            filt, ConsentScope.AGGREGATE_ANALYTICS, auth=_auth()
        )
    event = audit_sink.last()
    assert event.outcome == "scope_denied"
    assert event.result_row_count is None
    assert event.result_fingerprint is None


@pytest.mark.asyncio
async def test_insufficient_cohort_audited(audit_sink, tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        for i in range(5):
            s.add(_mk_row(f"tiny{i}"))
        s.commit()
        svc = build_audited_ledger_read_service(
            SqlLedgerReader(s),
            audit_sink=audit_sink,
            fallback_sink=JsonlFallbackSink(tmp_path),
            request_id_provider=lambda: "r",
        )
        with pytest.raises(InsufficientCohortError):
            await svc.get_aggregate_error_patterns(
                QueryFilters(cohort_id="cohortA"),
                ConsentScope.AGGREGATE_ANALYTICS,
                auth=_auth(),
            )
    event = audit_sink.last()
    assert event.outcome == "insufficient_cohort"
    assert event.k_anonymity_floor_applied == 50


@pytest.mark.asyncio
async def test_gateway_consent_denial_audited(audited_service, audit_sink):
    gw = JwtLedgerGateway(
        audited_service,
        _DenyConsent(),
        PrivacyPolicy(),
    )
    filt = LearnerEvidenceFilter(learner_pseudo_id="L1")
    with pytest.raises(ScopeDeniedError):
        await gw.learner_evidence(_auth(), filt)
    event = audit_sink.last()
    assert event.outcome == "scope_denied"
    assert event.query_kind == "learner_evidence"


@pytest.mark.asyncio
async def test_audit_row_never_contains_raw_learner_id_in_hash(audited_service, audit_sink):
    filt = LearnerEvidenceFilter(learner_pseudo_id="L42_sensitive_marker")
    await audited_service.get_learner_evidence(
        filt, ConsentScope.SELF_LEARNER, auth=_auth()
    )
    row = audit_sink.last()
    assert "L42_sensitive_marker" not in row.query_params_hash
    assert "L42_sensitive_marker" not in json.dumps({"hash": row.query_params_hash})


@pytest.mark.asyncio
async def test_sql_sink_persists_row():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sql_sink = SqlAuditSink(factory)
    event = AuditEvent(
        query_id="q-1",
        at_utc=NOW,
        tenant_id=TENANT,
        subject_pseudo_id="L1",
        caller_kind="preceptor",
        scope="self.learner",
        query_kind="learner_evidence",
        query_params_hash="abc123",
        query_params_bytes=42,
        outcome="ok",
        duration_ms=12,
        result_row_count=3,
    )
    sql_sink.emit(event)
    with factory() as session:
        row = session.execute(
            select(LedgerReadAuditRow).where(LedgerReadAuditRow.query_id == "q-1")
        ).scalar_one()
        assert row.tenant_id == TENANT
        assert row.result_row_count == 3


@pytest.mark.asyncio
async def test_primary_sink_failure_fallback_captures(tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    fallback = JsonlFallbackSink(tmp_path)
    failing_sink = _FailingAuditSink()
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        for i in range(3):
            s.add(_mk_row(f"fb{i}"))
        s.commit()
        svc = build_audited_ledger_read_service(
            SqlLedgerReader(s),
            audit_sink=failing_sink,
            fallback_sink=fallback,
            request_id_provider=lambda: "req-fb",
        )
        svc._now = lambda: NOW  # type: ignore[method-assign]
        filt = LearnerEvidenceFilter(learner_pseudo_id="L1")
        result = await svc.get_learner_evidence(
            filt, ConsentScope.SELF_LEARNER, auth=_auth()
        )
    assert len(result) >= 0
    day = NOW.strftime("%Y-%m-%d")
    rows = fallback.read_lines(TENANT, day)
    assert len(rows) == 1
    assert rows[0]["query_kind"] == "learner_evidence"
    assert rows[0]["fallback_reason"] == "RuntimeError"
    assert rows[0]["tenant_id"] == TENANT
    assert len(failing_sink.events) == 0


def test_audit_orm_matches_migration_column_set():
    expected = {
        "query_id",
        "at_utc",
        "tenant_id",
        "subject_pseudo_id",
        "actor_subject_id",
        "caller_kind",
        "scope",
        "query_kind",
        "query_params_hash",
        "query_params_bytes",
        "outcome",
        "result_row_count",
        "k_anonymity_floor_applied",
        "error_kind",
        "request_id",
        "duration_ms",
        "result_fingerprint",
    }
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("ledger_read_audit")}
    assert cols == expected
