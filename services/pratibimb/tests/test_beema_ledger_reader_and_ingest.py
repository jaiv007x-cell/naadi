from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.beema.analytics.error_dna import ErrorDNAUpdater
from services.beema.analytics.skill_decay import SkillDecayUpdater
from services.beema.ingest.consumer import BeemaIngestConfig, BeemaIngestor
from services.beema.ledger.interface import (
    ConfirmationState,
    LedgerQuery,
)
from services.beema.state.cursor_store import InMemoryCursorStore
from services.pratibimb.ledger.models import Base, SessionLedgerRow, TrustedPhysioVersion
from services.pratibimb.ledger.reader import SqlLedgerReader


def _mk_row(
    *,
    session_id: str,
    cohort_id: str,
    learner_pseudo_id: str,
    physio_engine_version: str,
    rubric_version: str,
    confirmation: str,
):
    return SessionLedgerRow(
        session_id=session_id,
        learner_pseudo_id=learner_pseudo_id,
        cohort_id=cohort_id,
        case_id="case1",
        case_version="1.0",
        physio_engine_version=physio_engine_version,
        rubric_version=rubric_version,
        replay_hash="hash123",
        finalized_at_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
        confirmation=confirmation,
        preceptor_pseudo_id=None,
        grade_total=0.8,
        grade_passed=True,
        axis_normalized={"ACTION": 0.9},
        evidence=[
            {
                "hit_id": "h1",
                "axis": "action",
                "matcher": "order_placed",
                "matched": True,
                "awarded": 1.0,
                "weight": 1.0,
                "at_sim_time_s": 60.0,
                "critical": False,
                "negative": False,
            }
        ],
        flags=[
            {
                "flag": "hypotension",
                "cause": "drug.morphine",
                "set_at_s": 120.0,
                "cleared_at_s": 200.0,
                "clear_reason": None,
                "caused_by_action_id": "give_morphine",
            }
        ],
        actions=[
            {
                "action_id": "give_morphine",
                "kind": "drug",
                "at_sim_time_s": 60.0,
                "canonical_key": "morphine:IV:10mg",
                "within_expected_window": True,
            }
        ],
        outcome_link_token=None,
    )


def test_sql_ledger_reader_query_and_decode():
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        s.add(_mk_row(
            session_id="b",
            cohort_id="cohortA",
            learner_pseudo_id="L1",
            physio_engine_version="physio@v1",
            rubric_version="rub1",
            confirmation="unconfirmed",
        ))
        s.add(_mk_row(
            session_id="c",
            cohort_id="cohortA",
            learner_pseudo_id="L1",
            physio_engine_version="physio@v1",
            rubric_version="rub1",
            confirmation="confirmed",
        ))
        s.commit()

        reader = SqlLedgerReader(s)
        page = reader.query(LedgerQuery(cohort_id="cohortA", limit=1, cursor=None))

        assert len(page.records) == 1
        assert page.records[0].session_id in {"b", "c"}
        assert page.next_cursor is not None

        page2 = reader.query(
            LedgerQuery(cohort_id="cohortA", limit=1, cursor=page.next_cursor)
        )
        assert len(page2.records) == 1
        assert page2.records[0].session_id in {"b", "c"}
        assert page2.records[0].session_id != page.records[0].session_id

        r = reader.get(page2.records[0].session_id)
        assert r is not None
        assert r.flags[0].flag == "hypotension"
        assert r.flags[0].cause == "drug.morphine"  # unknown enum -> kept as string
        assert isinstance(r.evidence[0].awarded, float)


def test_beema_ingest_consumer_wires_and_filters():
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as s:
        s.add(TrustedPhysioVersion(version="physio@v1", approved_by="test"))
        s.add(TrustedPhysioVersion(version="physio@v2", approved_by="test"))
        s.add(_mk_row(
            session_id="s1",
            cohort_id="cohortA",
            learner_pseudo_id="L1",
            physio_engine_version="physio@v1",
            rubric_version="rub1",
            confirmation="unconfirmed",
        ))
        s.add(_mk_row(
            session_id="s2",
            cohort_id="cohortA",
            learner_pseudo_id="L1",
            physio_engine_version="physio@v2",
            rubric_version="rub1",
            confirmation="confirmed",
        ))
        s.add(_mk_row(
            session_id="s3",
            cohort_id="cohortA",
            learner_pseudo_id="L1",
            physio_engine_version="physio@v1",
            rubric_version="rub1",
            confirmation="disputed",
        ))
        s.commit()

        reader = SqlLedgerReader(s)
        cursor_store = InMemoryCursorStore()
        skill_decay = SkillDecayUpdater()
        error_dna = ErrorDNAUpdater()

        cfg = BeemaIngestConfig(
            cohort_ids=("cohortA",),
            beema_trusted_physio_versions=frozenset({"physio@v1"}),  # only v1 is trusted by BEEMA
            accept_unconfirmed=True,  # include unconfirmed for this test
            page_size=10,
        )
        ingestor = BeemaIngestor(
            reader=reader,
            cursor_store=cursor_store,
            skill_decay=skill_decay,
            error_dna=error_dna,
            cfg=cfg,
        )

        n = ingestor.run_once()
        assert n == 1  # only s1 passes trusted_versions + accept + not disputed
        assert skill_decay.last_processed() == "s1"
        assert error_dna.last_processed() == "s1"

