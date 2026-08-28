"""SAMVAAD.e — 11-row / 14-test frozen acceptance matrix."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from services.pratibimb.ledger.models import (
    Base,
    SamvaadFormativeEvidenceRow,
    SamvaadSummativeEvidenceRow,
)
from services.pratibimb.samvaad.bias_remediation import (
    ERROR_KIND_ILLEGAL_TRANSITION,
    BiasRemediationError,
)
from services.pratibimb.samvaad.dhaara_projection import (
    InMemoryDhaaraProjection,
    projection_bytes,
    rebuild_projection,
    reconcile_projection,
)
from services.pratibimb.samvaad.formative import (
    capture_formative,
    clear_formative_store,
)
from services.pratibimb.samvaad.formative_projector import (
    formative_evidence_digest,
)
from services.pratibimb.samvaad.ledger_projector import clear_summative_ledger
from services.pratibimb.samvaad.metrics import (
    record_dhaara_publish,
    record_dhaara_reconcile,
    record_evidence_insert,
    render_samvaad_prometheus_metrics,
    reset_samvaad_metrics_for_tests,
)
from services.pratibimb.samvaad.summative import capture_summative
from services.pratibimb.samvaad.verify_service import verify_samvaad

pytestmark = pytest.mark.samvaad_e_acceptance

_REPO = Path(__file__).resolve().parents[3]
_PRATIBIMB = _REPO / "services" / "pratibimb"
_CAPTURED = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)


@pytest.fixture()
def ledger_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_e_state():
    clear_formative_store()
    clear_summative_ledger()
    reset_samvaad_metrics_for_tests()
    yield
    clear_formative_store()
    clear_summative_ledger()
    reset_samvaad_metrics_for_tests()


def _summative_payload(**overrides):
    hits = ["clinical.empathy", "clinical.handoff"]
    payload = {
        "transcript_digest": "digest-021",
        "grader_version": "grader-v1",
        "rubric_schema_version": "rubric-v1",
        "artifact_schema_version": "artifact-v1",
        "evidence_class": "machine_sim",
        "learner_pseudo_id": "learner-1",
        "session_anchor": "session-021",
        "competency_hits": hits,
        "freshness_inputs": _freshness_inputs(hits),
    }
    payload.update(overrides)
    return payload


def _formative_payload(**overrides):
    hits = ["clinical.empathy", "clinical.handoff"]
    payload = {
        "evidence_class": "patient_reported",
        "learner_pseudo_id": "learner-1",
        "source_context": {"instrument": "teach_back"},
        "submitted_by": "patient-opaque",
        "session_anchor": "session-022",
        "matcher_parameters": {"matched": True, "window_ms": 5000},
        "competency_hits": hits,
        "freshness_inputs": _freshness_inputs(hits),
    }
    payload.update(overrides)
    return payload


def _freshness_inputs(hits):
    return {
        competency_id: {
            "competency_before": 0.4,
            "competency_after": 0.6,
            "ability": 0.58,
            "confidence": 0.72,
            "freshness": 0.91,
            "evidence_age_days": 0.0,
            "reassessment_due": False,
        }
        for competency_id in hits
    }


def _insert_summative(
    session: Session,
    *,
    payload=None,
    sink=None,
    evidence_id="evidence-021",
    captured_at=_CAPTURED,
):
    from services.pratibimb.samvaad.ledger_projector import insert_summative_evidence

    return insert_summative_evidence(
        tenant_id="tenant-1",
        record=capture_summative(payload or _summative_payload()),
        session=session,
        projection_sink=sink,
        evidence_id=evidence_id,
        captured_at_utc=captured_at,
    )


def _insert_formative(
    session: Session,
    *,
    payload=None,
    sink=None,
    evidence_id="evidence-022",
    captured_at=_CAPTURED,
):
    return capture_formative(
        payload or _formative_payload(),
        tenant_id="tenant-1",
        session=session,
        projection_sink=sink,
        evidence_id=evidence_id,
        captured_at_utc=captured_at,
    )


def test_samvaad_e_1_postgres_insert_021(ledger_session):
    row = _insert_summative(ledger_session)
    stored = ledger_session.get(SamvaadSummativeEvidenceRow, row.evidence_id)
    assert stored is not None
    assert (stored.tenant_id, stored.assessment_kind) == ("tenant-1", "summative")
    assert stored.learner_pseudo_id == "learner-1"
    assert stored.session_anchor == "session-021"
    assert json.loads(stored.competency_hits_json) == [
        "clinical.empathy",
        "clinical.handoff",
    ]
    migration = (
        _PRATIBIMB
        / "ledger/migrations/023_samvaad_tenant_dedup_and_freshness.sql"
    ).read_text(encoding="utf-8")
    assert "UNIQUE (tenant_id, transcript_digest)" in migration


def test_samvaad_e_2_postgres_insert_022(ledger_session):
    record = _insert_formative(ledger_session)
    stored = ledger_session.get(SamvaadFormativeEvidenceRow, record.evidence_id)
    assert stored is not None
    assert (stored.tenant_id, stored.assessment_kind) == ("tenant-1", "formative")
    assert stored.learner_pseudo_id == "learner-1"
    assert stored.evidence_digest
    assert json.loads(stored.competency_hits_json) == [
        "clinical.empathy",
        "clinical.handoff",
    ]
    assert ledger_session.scalars(select(SamvaadSummativeEvidenceRow)).all() == []
    migration = (
        _PRATIBIMB
        / "ledger/migrations/023_samvaad_tenant_dedup_and_freshness.sql"
    ).read_text(encoding="utf-8")
    assert "UNIQUE (tenant_id, evidence_digest)" in migration


def test_samvaad_e_3a_duplicate_021_uses_illegal_transition(ledger_session):
    _insert_summative(ledger_session)
    with pytest.raises(BiasRemediationError) as exc:
        _insert_summative(
            ledger_session,
            evidence_id="evidence-021-retry",
            captured_at=_CAPTURED + timedelta(seconds=1),
        )
    assert exc.value.error_kind == ERROR_KIND_ILLEGAL_TRANSITION


def test_samvaad_e_3b_duplicate_022_uses_illegal_transition(ledger_session):
    _insert_formative(ledger_session)
    with pytest.raises(BiasRemediationError) as exc:
        _insert_formative(
            ledger_session,
            evidence_id="evidence-022-retry",
            captured_at=_CAPTURED + timedelta(days=1),
        )
    assert exc.value.error_kind == ERROR_KIND_ILLEGAL_TRANSITION


def test_samvaad_e_4_dhaara_event_after_021_commit(ledger_session):
    sink = InMemoryDhaaraProjection()
    _insert_summative(ledger_session, sink=sink)
    events = [projection.event_dict() for projection in sink.ordered()]
    assert len(events) == 2
    assert all(event["type"] == "freshness.snapshot" for event in events)
    assert all(event["assessment_kind"] == "summative" for event in events)
    assert {event["competency_id"] for event in events} == {
        "clinical.empathy",
        "clinical.handoff",
    }


def test_samvaad_e_5_dhaara_event_after_022_commit(ledger_session):
    sink = InMemoryDhaaraProjection()
    _insert_formative(ledger_session, sink=sink)
    events = [projection.event_dict() for projection in sink.ordered()]
    assert len(events) == 2
    assert all(event["assessment_kind"] == "formative" for event in events)
    assert {event["competency_id"] for event in events} == {
        "clinical.empathy",
        "clinical.handoff",
    }


def test_samvaad_e_6_class_aware_weight(ledger_session):
    sink = InMemoryDhaaraProjection()
    _insert_summative(
        ledger_session,
        payload=_summative_payload(evidence_class="preceptor_attested"),
        sink=sink,
    )
    snapshot = sink.ordered()[0].snapshot
    assert snapshot.evidence_class == "preceptor_attested"
    assert snapshot.evidence_weight == 1.35
    assert snapshot.ability == 0.58
    assert snapshot.confidence == 0.72
    assert snapshot.freshness == 0.91
    missing_inputs = _summative_payload(transcript_digest="digest-no-inputs")
    missing_inputs.pop("freshness_inputs")
    with pytest.raises(ValueError, match="freshness_inputs"):
        _insert_summative(
            ledger_session,
            payload=missing_inputs,
            evidence_id="evidence-no-inputs",
        )
    assert len(ledger_session.scalars(select(SamvaadSummativeEvidenceRow)).all()) == 1


def test_samvaad_e_7_prometheus_closed_labels():
    record_evidence_insert(
        assessment_kind="summative",
        evidence_class="machine_sim",
        outcome="success",
    )
    record_dhaara_publish(
        assessment_kind="formative",
        evidence_class="patient_reported",
        outcome="skipped",
    )
    record_dhaara_reconcile(assessment_kind="formative", outcome="success")
    rendered = render_samvaad_prometheus_metrics()
    assert "samvaad_evidence_insert_total" in rendered
    assert "samvaad_dhaara_publish_total" in rendered
    assert "samvaad_dhaara_reconcile_total" in rendered
    assert all(
        forbidden not in rendered
        for forbidden in ("tenant_id", "session_id", "evidence_digest", "competency_id")
    )
    with pytest.raises(ValueError):
        record_evidence_insert(
            assessment_kind="summative",
            evidence_class="tenant-1",
            outcome="success",
        )


def test_samvaad_e_8_prior_compose_literal_and_cf2_call_site():
    test_files = [
        "services/pratibimb/tests/test_samvaad_a.py",
        "services/pratibimb/tests/test_samvaad_b.py",
        "services/pratibimb/tests/test_samvaad_c.py",
        "services/pratibimb/tests/test_samvaad_d.py",
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *test_files, "-q", "--tb=no"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(r"\b65 passed\b", proc.stdout)
    assert 22 + 14 + 18 + 11 == 65

    sites = []
    for path in _PRATIBIMB.rglob("*.py"):
        if path.name == "verifier_emit.py" or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "emit_samvaad_verifier_audit"
            ) or (
                isinstance(func, ast.Attribute)
                and func.attr == "emit_samvaad_verifier_audit"
            ):
                sites.append(str(path.relative_to(_REPO)).replace("\\", "/"))
                break
    assert sites == ["services/pratibimb/samvaad/verify_service.py"]


def test_samvaad_e_9a_deterministic_021_materialization():
    def materialize():
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            row = _insert_summative(session)
            value = json.dumps(row.__dict__, sort_keys=True, default=str)
        engine.dispose()
        clear_summative_ledger()
        return value

    assert materialize() == materialize()


def test_samvaad_e_9b_formative_digest_excludes_capture_time():
    common = _formative_payload()

    def digest(payload):
        return formative_evidence_digest(
            learner_pseudo_id=payload["learner_pseudo_id"],
            evidence_class=payload["evidence_class"],
            source_context=payload["source_context"],
            submitted_by=payload["submitted_by"],
            session_anchor=payload["session_anchor"],
            matcher_parameters=payload["matcher_parameters"],
            competency_hits=payload["competency_hits"],
        )

    first = digest(common)
    assert first == digest(dict(common))
    assert first != digest({**common, "learner_pseudo_id": "learner-2"})
    assert first != digest({**common, "session_anchor": "session-other"})
    # captured_at_utc is deliberately not an accepted digest input.
    assert "captured_at_utc" not in formative_evidence_digest.__annotations__


def test_samvaad_e_9c_rebuild_is_byte_identical_and_ordered(ledger_session):
    _insert_formative(
        ledger_session,
        evidence_id="evidence-z",
        captured_at=_CAPTURED,
    )
    _insert_summative(
        ledger_session,
        evidence_id="evidence-a",
        captured_at=_CAPTURED - timedelta(seconds=1),
    )
    first_projection, first_states = rebuild_projection(ledger_session)
    second_projection, second_states = rebuild_projection(ledger_session)
    assert projection_bytes(first_projection) == projection_bytes(second_projection)
    assert [state.model_dump_json() for state in first_states] == [
        state.model_dump_json() for state in second_states
    ]
    assert [item.evidence_id for item in first_projection] == [
        "evidence-a",
        "evidence-a",
        "evidence-z",
        "evidence-z",
    ]
    assert [item.snapshot.competency_id for item in first_projection[:2]] == sorted(
        item.snapshot.competency_id for item in first_projection[:2]
    )


def test_samvaad_e_10_exact_reconciliation_heals_and_skips(ledger_session):
    _insert_formative(ledger_session)

    class FailingSink(InMemoryDhaaraProjection):
        def publish(self, projection):
            raise OSError("Dhaara unavailable")

    failed = reconcile_projection(ledger_session, FailingSink(), now=_CAPTURED)
    sink = InMemoryDhaaraProjection()
    first = reconcile_projection(ledger_session, sink, now=_CAPTURED)
    second = reconcile_projection(ledger_session, sink, now=_CAPTURED)
    assert failed == {"success": 0, "skipped": 0, "error": 2}
    assert first == {"success": 2, "skipped": 0, "error": 0}
    assert second == {"success": 0, "skipped": 2, "error": 0}
    assert len(sink.keys()) == 2


def test_samvaad_e_11_d_routing_regression_under_sql_backend(
    ledger_session, monkeypatch
):
    import services.pratibimb.samvaad.verify_service as service

    monkeypatch.setattr(service, "SAMVAAD_LIVE_WRITE_ALLOW", True)
    formative = verify_samvaad(
        {
            "assessment_kind": "formative",
            "payload": {
                "evidence_class": "patient_reported",
                "source_context": {"instrument": "teach_back_survey"},
                "submitted_by": "patient-opaque-17",
                "session_anchor": "session-022",
                "matcher_parameters": {"window_ms": 5000, "matched": True},
            },
        },
        tenant_id="tenant-1",
        ledger_session=ledger_session,
        learner_pseudo_id="learner-1",
    )
    summative = verify_samvaad(
        {
            "assessment_kind": "summative",
            "dry_run": False,
            "payload": {
                key: value
                for key, value in _summative_payload().items()
                if key not in {"learner_pseudo_id", "session_anchor"}
            },
        },
        tenant_id="tenant-1",
        ledger_session=ledger_session,
        learner_pseudo_id="learner-1",
        session_anchor="session-021",
        freshness_inputs=_freshness_inputs(
            ["clinical.empathy", "clinical.handoff"]
        ),
    )
    assert formative["assessment_kind"] == "formative"
    assert summative["inserted"] is True
    assert len(ledger_session.scalars(select(SamvaadFormativeEvidenceRow)).all()) == 1
    assert len(ledger_session.scalars(select(SamvaadSummativeEvidenceRow)).all()) == 1
