"""LedgerReadService privacy layer — consent scopes and k-anonymity."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.pratibimb.ledger.models import (
    Base,
    CohortUnitAssignmentRow,
    InstitutionalUnit,
    SessionLedgerRow,
    TrustedPhysioVersion,
)
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger_read.errors import InsufficientCohortError
from services.pratibimb.ledger_read.service import LedgerReadService
from services.pratibimb.ledger.unit_mapping import UnknownUnitError
from shared.schemas.ledger_read import (
    ConsentScope,
    LearnerEvidenceFilter,
    QueryFilters,
)
from shared.schemas.flag_cause import FlagCause
from shared.schemas.unit_ref import UnitType


def _mk_row(
    *,
    session_id: str,
    learner: str = "L1",
    cohort: str = "cohortA",
    flags: list | None = None,
    evidence: list | None = None,
    finalized_at: datetime | None = None,
) -> SessionLedgerRow:
    return SessionLedgerRow(
        session_id=session_id,
        learner_pseudo_id=learner,
        cohort_id=cohort,
        case_id="anaphylaxis_01",
        case_version="1.0",
        physio_engine_version="physio@v1",
        rubric_version="rub1",
        replay_hash=f"hash-{session_id}",
        finalized_at_utc=finalized_at or datetime(2026, 8, 1, tzinfo=timezone.utc),
        confirmation="confirmed",
        preceptor_pseudo_id=None,
        grade_total=0.8,
        grade_passed=True,
        axis_normalized={"SAFETY": 0.9},
        evidence=evidence or [],
        flags=flags
        or [
            {
                "flag": "hypotension",
                "cause": FlagCause.DRUG_PD_EFFECT.value,
                "set_at_s": 60.0,
                "cleared_at_s": 120.0,
                "clear_reason": None,
            }
        ],
        actions=[],
        outcome_link_token=None,
    )


@pytest.fixture
def read_service():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        for i in range(55):
            s.add(_mk_row(session_id=f"s{i}", learner=f"L{i % 5}"))
        s.add(
            _mk_row(
                session_id="dense",
                flags=[
                    {
                        "flag": "hypotension",
                        "cause": FlagCause.DRUG_PD_EFFECT.value,
                        "set_at_s": 1.0,
                        "cleared_at_s": 2.0,
                        "clear_reason": None,
                    }
                    for _ in range(12)
                ],
            )
        )
        s.commit()
        reader = SqlLedgerReader(s)
        yield LedgerReadService(reader)


@pytest.mark.asyncio
async def test_learner_evidence_requires_individual_consent_scope(read_service):
    filt = LearnerEvidenceFilter(learner_pseudo_id="L1")
    with pytest.raises(PermissionError, match="cannot access individual evidence"):
        await read_service.get_learner_evidence(filt, ConsentScope.AGGREGATE_ANALYTICS)


@pytest.mark.asyncio
async def test_learner_evidence_returns_provenance_views(read_service):
    filt = LearnerEvidenceFilter(learner_pseudo_id="L1")
    views = await read_service.get_learner_evidence(filt, ConsentScope.SELF_LEARNER)
    assert views
    assert views[0].session_id
    assert views[0].cohort_id == "cohortA"
    assert views[0].content_hash.startswith("hash-")
    assert views[0].flags[0]["namespace"] == "drug"


@pytest.mark.asyncio
async def test_cause_prefix_filters_learner_evidence(read_service):
    filt = LearnerEvidenceFilter(learner_pseudo_id="L1", cause_prefix="clinical.")
    views = await read_service.get_learner_evidence(filt, ConsentScope.PRECEPTOR_REVIEW)
    assert views == []


@pytest.mark.asyncio
async def test_aggregate_requires_aggregate_scope(read_service):
    with pytest.raises(PermissionError, match="aggregate error patterns"):
        await read_service.get_aggregate_error_patterns(
            QueryFilters(cohort_id="cohortA"),
            ConsentScope.SELF_LEARNER,
        )


@pytest.mark.asyncio
async def test_aggregate_enforces_k_anonymity_floor():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        for i in range(10):
            s.add(_mk_row(session_id=f"small-{i}"))
        s.commit()
        svc = LedgerReadService(SqlLedgerReader(s))
        with pytest.raises(InsufficientCohortError) as exc:
            await svc.get_aggregate_error_patterns(
                QueryFilters(cohort_id="cohortA"),
                ConsentScope.AGGREGATE_ANALYTICS,
            )
        assert exc.value.minimum_required == 50
        assert "10" not in str(exc.value)


@pytest.mark.asyncio
async def test_aggregate_patterns_expose_session_and_occurrence_counts(read_service):
    report = await read_service.get_aggregate_error_patterns(
        QueryFilters(cohort_id="cohortA"),
        ConsentScope.AGGREGATE_ANALYTICS,
    )
    assert report.cohort_size >= 50
    pd = next(p for p in report.patterns if p["cause"] == FlagCause.DRUG_PD_EFFECT.value)
    assert pd["occurrence_count"] >= pd["session_count"]
    assert pd["session_count"] >= 50


@pytest.mark.asyncio
async def test_unit_safety_report_resolves_typed_unit_mapping():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    tenant = "tenant-virohan"
    critical_ev = [
        {
            "hit_id": "safe.no_iv_epi_bolus",
            "axis": "safety",
            "matcher": "drug_given",
            "matched": False,
            "awarded": 0.0,
            "weight": 10.0,
            "at_sim_time_s": 90.0,
            "critical": True,
        }
    ]
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        s.add(
            InstitutionalUnit(
                tenant_id=tenant,
                unit_id="ward-emergency",
                unit_type=UnitType.WARD.value,
                display_name="Emergency Ward",
            )
        )
        s.add(
            CohortUnitAssignmentRow(
                tenant_id=tenant, cohort_id="cohortA", unit_id="ward-emergency"
            )
        )
        s.add(
            CohortUnitAssignmentRow(
                tenant_id=tenant, cohort_id="cohortB", unit_id="ward-emergency"
            )
        )
        for i in range(30):
            s.add(_mk_row(session_id=f"a{i}", cohort="cohortA", finalized_at=now))
        for i in range(25):
            s.add(
                _mk_row(
                    session_id=f"b{i}",
                    cohort="cohortB",
                    finalized_at=now,
                    evidence=critical_ev if i < 5 else [],
                )
            )
        s.commit()
        svc = LedgerReadService(SqlLedgerReader(s))
        report = await svc.get_unit_safety_report(
            tenant,
            "ward-emergency",
            window_days=30,
            scope=ConsentScope.AGGREGATE_ANALYTICS,
        )
        assert report.cohort_size == 55
        assert report.critical_violation_rate == pytest.approx(5 / 55, rel=1e-3)


@pytest.mark.asyncio
async def test_unit_safety_report_unknown_unit_raises():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        s.commit()
        svc = LedgerReadService(SqlLedgerReader(s))
        with pytest.raises(UnknownUnitError):
            await svc.get_unit_safety_report(
                "tenant-virohan",
                "missing-unit",
                window_days=30,
                scope=ConsentScope.AGGREGATE_ANALYTICS,
            )


@pytest.mark.asyncio
async def test_cohort_summary(read_service):
    summary = await read_service.get_cohort_summary(
        "cohortA", scope=ConsentScope.AGGREGATE_ANALYTICS
    )
    assert summary.cohort_id == "cohortA"
    assert summary.cohort_size >= 50
