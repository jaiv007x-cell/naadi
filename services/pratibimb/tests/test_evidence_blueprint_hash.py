"""Phase E1 close: EvidenceEventView.blueprint_content_hash + REST case-pick validation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.pratibimb.app.main import app
from services.pratibimb.authoring.constants import PolicyRejectionCode
from services.pratibimb.ledger.models import (
    Base,
    SessionLedgerRow,
    TrustedPhysioVersion,
)
from services.pratibimb.ledger.reader import SqlLedgerReader
from services.pratibimb.ledger_read.service import LedgerReadService
from shared.schemas.flag_cause import FlagCause
from shared.schemas.ledger_read import ConsentScope, LearnerEvidenceFilter
from shared.schemas.session import CreateSessionRequest

HASH_A = "a" * 64


def _mk_row(
    *,
    session_id: str,
    learner: str = "L1",
    blueprint_content_hash: str | None = None,
) -> SessionLedgerRow:
    return SessionLedgerRow(
        session_id=session_id,
        learner_pseudo_id=learner,
        cohort_id="cohortA",
        case_id="anaphylaxis_01",
        case_version="1.0",
        physio_engine_version="physio@v1",
        rubric_version="rub1",
        replay_hash=f"sha256:{session_id}",
        blueprint_content_hash=blueprint_content_hash,
        finalized_at_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
        confirmation="confirmed",
        preceptor_pseudo_id=None,
        grade_total=0.8,
        grade_passed=True,
        axis_normalized={"SAFETY": 0.9},
        evidence=[],
        flags=[
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
def evidence_service():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        s.add(_mk_row(session_id="stamped", blueprint_content_hash=HASH_A))
        s.add(_mk_row(session_id="legacy", learner="L2", blueprint_content_hash=None))
        # Retired-version case: stamp still present on the ledger row (write-time).
        s.add(
            _mk_row(
                session_id="retired-version",
                learner="L3",
                blueprint_content_hash=HASH_A,
            )
        )
        s.commit()
        yield LedgerReadService(SqlLedgerReader(s))


@pytest.mark.asyncio
async def test_evidence_view_surfaces_write_time_blueprint_hash(evidence_service):
    views = await evidence_service.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="L1"),
        ConsentScope.SELF_LEARNER,
    )
    assert len(views) == 1
    assert views[0].blueprint_content_hash == HASH_A
    assert views[0].content_hash.startswith("sha256:")
    assert views[0].content_hash != views[0].blueprint_content_hash


@pytest.mark.asyncio
async def test_evidence_view_legacy_null_blueprint_hash(evidence_service):
    views = await evidence_service.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="L2"),
        ConsentScope.SELF_LEARNER,
    )
    assert len(views) == 1
    assert views[0].blueprint_content_hash is None
    assert views[0].content_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_evidence_view_retired_version_keeps_stamped_hash(evidence_service):
    """Stamp is on the ledger row — view does not re-resolve published/retired state."""
    views = await evidence_service.get_learner_evidence(
        LearnerEvidenceFilter(learner_pseudo_id="L3"),
        ConsentScope.SELF_LEARNER,
    )
    assert len(views) == 1
    assert views[0].blueprint_content_hash == HASH_A


def test_create_session_request_rejects_partial_pick():
    with pytest.raises(Exception):
        CreateSessionRequest(
            learner_id="L1",
            tenant_id="tenant-a",
            case_id="C1",
            # case_version missing
        )


def test_rest_partial_case_pick_returns_400_invalid_case_pick():
    client = TestClient(app)
    resp = client.post(
        "/v1/sessions",
        json={
            "learner_id": "L1",
            "tenant_id": "tenant-a",
            "case_id": "C1",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == PolicyRejectionCode.INVALID_CASE_PICK.value
    assert "together" in body["message"]
