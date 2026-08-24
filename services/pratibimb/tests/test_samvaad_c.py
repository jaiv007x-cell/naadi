"""SAMVAAD.c — 16-case acceptance matrix."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.samvaad_c_acceptance

from services.pratibimb.app.eval import matcher_registry
from services.pratibimb.app.eval import nirikshak as nirikshak_mod
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.samvaad.bias_remediation import (
    ERROR_KIND_ILLEGAL_TRANSITION,
    BiasRemediationError,
    assert_valid_transition,
)
from services.pratibimb.samvaad.constants import CITATION_STATE_A_ENFORCED
from services.pratibimb.samvaad.corpus import SamvaadCorpus, load_phase1_digital_literacy
from services.pratibimb.samvaad.eval_harness import SamvaadEvalHarness, SamvaadRubricHit
from services.pratibimb.samvaad.evidence_class import EvidenceClass
from services.pratibimb.samvaad.formative import capture_formative, clear_formative_store
from services.pratibimb.samvaad.ledger_projector import (
    clear_summative_ledger,
    insert_summative_evidence,
    summative_ledger_rows,
)
from services.pratibimb.samvaad.matcher_kinds import SAMVAAD_MATCHER_KINDS
from services.pratibimb.samvaad.matcher_spec import COMBINATOR, REQUIRED_STEPS
from services.pratibimb.samvaad.metrics import (
    REASON_PATIENT_REPORTED,
    SUMMATIVE_REJECTED_COUNTER,
    reset_summative_metrics,
    summative_rejected_total,
)
from services.pratibimb.samvaad.nirikshak_matchers import SAMVAAD_MATCHERS
from services.pratibimb.samvaad.registry import assert_samvaad_registry_exact, merged_matcher_kinds
from services.pratibimb.samvaad.runtime import evaluate_action_sequence, evaluate_empathy_and
from services.pratibimb.samvaad.summative import capture_summative, clear_summative_store
from services.pratibimb.samvaad.summative_contract import (
    ERROR_KIND_SUMMATIVE_EVIDENCE_CLASS_FORBIDDEN,
    SummativeEvidenceRejectedError,
)
from services.pratibimb.samvaad.verifier_emit import (
    B_STUB_EMIT_PARAM_KEYS,
    clear_verifier_emits,
    emit_samvaad_verifier_audit,
    verifier_emits,
)
from services.pratibimb.samvaad.verify_service import emit_param_keys_for_matrix, verify_samvaad

_REPO = Path(__file__).resolve().parents[3]
_SAMVAAD = _REPO / "services" / "pratibimb" / "samvaad"


@pytest.fixture(autouse=True)
def _clean_stores():
    clear_summative_store()
    clear_formative_store()
    clear_summative_ledger()
    clear_verifier_emits()
    reset_summative_metrics()
    yield
    clear_summative_store()
    clear_formative_store()
    clear_summative_ledger()
    clear_verifier_emits()
    reset_summative_metrics()


def _valid_summative(**overrides):
    base = {
        "transcript_digest": "abc" * 21 + "ab",
        "grader_version": "sealed-stub.v1",
        "rubric_schema_version": "samvaad.reference_rubrics.v1.1",
        "artifact_schema_version": "samvaad.summative.v1",
        "evidence_class": EvidenceClass.MACHINE_SIM.value,
        "competency_hits": ["digi.patient_id.two_source_verify"],
        "framework_citation_anchor": CITATION_STATE_A_ENFORCED,
    }
    base.update(overrides)
    return base


def test_samvaad_c_1_registry_merge_exact():
    assert_samvaad_registry_exact()
    assert set(SAMVAAD_MATCHERS.keys()) == set(SAMVAAD_MATCHER_KINDS)
    assert len(SAMVAAD_MATCHERS) == len(SAMVAAD_MATCHER_KINDS)
    for kind in SAMVAAD_MATCHER_KINDS:
        assert kind in nirikshak_mod._MATCHERS
        assert kind in matcher_registry.KNOWN_MATCHER_KINDS
    assert merged_matcher_kinds() == frozenset(nirikshak_mod._MATCHERS.keys())


def test_samvaad_c_2_empathy_plus_via_nirikshak():
    empathy = next(
        r
        for r in SamvaadCorpus().all()
        if r["competency"] == "comm.empathy.acknowledge_distress"
    )
    harness = SamvaadEvalHarness(
        {
            "step_results": {s: True for s in REQUIRED_STEPS},
            "interruptions_after_ack": 0,
        }
    )
    hit = SamvaadRubricHit(params=empathy["params"], matcher=empathy["matcher"])
    ok, ev = harness.evaluate_kind("action_sequence", hit)
    assert ok is True
    assert ev["combinator"] == COMBINATOR


def test_samvaad_c_3_empathy_minus_via_nirikshak():
    empathy = next(
        r
        for r in SamvaadCorpus().all()
        if r["competency"] == "comm.empathy.acknowledge_distress"
    )
    harness = SamvaadEvalHarness(
        {
            "step_results": {s: (s != "teach_back_or_check_understanding") for s in REQUIRED_STEPS},
            "interruptions_after_ack": 0,
        }
    )
    hit = SamvaadRubricHit(params=empathy["params"], matcher=empathy["matcher"])
    ok, _ = harness.evaluate_kind("action_sequence", hit)
    assert ok is False


def test_samvaad_c_4_digital_literacy_via_nirikshak():
    pid = next(
        r
        for r in load_phase1_digital_literacy()
        if r["competency"] == "digi.patient_id.two_source_verify"
    )
    required = pid["matcher"]["required"]
    harness = SamvaadEvalHarness(
        {"observed_steps": ["scan_wristband", "confirm_patient_on_screen", "draw"]}
    )
    hit = SamvaadRubricHit(params=pid.get("params", {}), matcher=pid["matcher"])
    ok, ev = harness.evaluate_kind("action_sequence", hit)
    assert ok is True
    assert ev["required"] == required


def test_samvaad_c_5_coexistence_with_b_goldens():
    steps = {s: True for s in REQUIRED_STEPS}
    before_empathy, ev1 = evaluate_empathy_and(steps, interruptions_after_ack=0)
    h = load_phase1_digital_literacy()
    pid = next(r for r in h if r["competency"] == "digi.patient_id.two_source_verify")
    before_seq, ev2 = evaluate_action_sequence(
        ["scan_wristband", "confirm_patient_on_screen"],
        pid["matcher"]["required"],
    )
    assert set(SAMVAAD_MATCHER_KINDS) <= set(nirikshak_mod._MATCHERS.keys())
    after_empathy, ev1b = evaluate_empathy_and(steps, interruptions_after_ack=0)
    after_seq, ev2b = evaluate_action_sequence(
        ["scan_wristband", "confirm_patient_on_screen"],
        pid["matcher"]["required"],
    )
    assert (before_empathy, ev1) == (after_empathy, ev1b)
    assert (before_seq, ev2) == (after_seq, ev2b)


def test_samvaad_c_6_formative_accepts_patient_reported():
    rec = capture_formative(
        {"evidence_class": EvidenceClass.PATIENT_REPORTED.value, "note": "formative-only"}
    )
    assert rec.evidence_class == EvidenceClass.PATIENT_REPORTED.value


def test_samvaad_c_7_summative_rejects_patient_reported_422_counter():
    with pytest.raises(SummativeEvidenceRejectedError) as exc:
        capture_summative(_valid_summative(evidence_class=EvidenceClass.PATIENT_REPORTED.value))
    assert exc.value.error_kind == ERROR_KIND_SUMMATIVE_EVIDENCE_CLASS_FORBIDDEN
    assert summative_rejected_total(reason=REASON_PATIENT_REPORTED) == 1
    assert SUMMATIVE_REJECTED_COUNTER == "samvaad_summative_rejected_total"


def test_samvaad_c_7b_summative_rejects_patient_reported_route_422():
    """Row #7 route axis — observe HTTP 422, not compose-from-mapping."""
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from services.pratibimb.app.api.deps import get_auth_context
    from services.pratibimb.app.main import app
    from services.pratibimb.auth.context import AuthContext

    def _auth() -> AuthContext:
        now = datetime.now(timezone.utc)
        return AuthContext(
            subject_pseudo_id="samvaad-c-7b",
            tenant_id="t-samvaad-c",
            jti="jti-7b",
            issued_at=now,
            expires_at=now,
            source="test",
        )

    class _NoopSink:
        def emit(self, _event: object) -> None:
            return None

    app.dependency_overrides[get_auth_context] = _auth
    try:
        with patch(
            "services.pratibimb.samvaad.routes.get_audit_sink",
            return_value=_NoopSink(),
        ):
            client = TestClient(app)
            resp = client.post(
                "/v1/samvaad/verify",
                json={
                    "assessment_kind": "summative",
                    "dry_run": True,
                    "payload": _valid_summative(
                        evidence_class=EvidenceClass.PATIENT_REPORTED.value
                    ),
                },
            )
    finally:
        app.dependency_overrides.pop(get_auth_context, None)

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == ERROR_KIND_SUMMATIVE_EVIDENCE_CLASS_FORBIDDEN


def test_samvaad_c_8_summative_accepts_machine_sim_and_preceptor():
    r1 = capture_summative(_valid_summative())
    r2 = capture_summative(
        _valid_summative(
            evidence_class=EvidenceClass.PRECEPTOR_ATTESTED.value,
            transcript_digest="def" * 21 + "de",
        )
    )
    insert_summative_evidence(tenant_id="t1", record=r1)
    insert_summative_evidence(tenant_id="t1", record=r2)
    ledger = summative_ledger_rows()
    assert len(ledger) == 2
    classes = {row["evidence_class"] for row in ledger}
    assert classes == {
        EvidenceClass.MACHINE_SIM.value,
        EvidenceClass.PRECEPTOR_ATTESTED.value,
    }
    for row in ledger:
        assert row["transcript_digest"]
        assert row["grader_version"]
        assert row["rubric_schema_version"]
        assert row["artifact_schema_version"]


def test_samvaad_c_9_dry_run_omitted_defaults_true_no_insert():
    out = verify_samvaad({"payload": _valid_summative()})
    assert out["dry_run"] is True
    assert out["inserted"] is False
    assert len(summative_ledger_rows()) == 0
    assert len(verifier_emits()) == 1
    assert verifier_emits()[0]["params"]["dry_run"] is True


def test_samvaad_c_10a_dry_run_false_allow_insert():
    with patch("services.pratibimb.samvaad.verify_service.SAMVAAD_LIVE_WRITE_ALLOW", True):
        out = verify_samvaad(
            {"dry_run": False, "payload": _valid_summative(transcript_digest="ghi" * 21 + "gh")}
        )
    assert out["inserted"] is True
    assert len(summative_ledger_rows()) == 1
    params = verifier_emits()[-1]["params"]
    assert params["dry_run"] is False
    assert params["would_mutate"] is True


def test_samvaad_c_10b_dry_run_false_no_allow_no_insert():
    with patch("services.pratibimb.samvaad.verify_service.SAMVAAD_LIVE_WRITE_ALLOW", False):
        out = verify_samvaad(
            {"dry_run": False, "payload": _valid_summative(transcript_digest="jkl" * 21 + "jk")}
        )
    assert out["inserted"] is False
    assert len(summative_ledger_rows()) == 0
    params = verifier_emits()[-1]["params"]
    assert params["dry_run"] is False
    assert params["would_mutate"] is False


def test_samvaad_c_11_exactly_one_production_ast_call():
    def _calls_emit(tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "emit_samvaad_verifier_audit":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "emit_samvaad_verifier_audit":
                return True
        return False

    sites: list[str] = []
    for path in (_REPO / "services" / "pratibimb").rglob("*.py"):
        if path.name == "verifier_emit.py":
            continue
        if "tests" in path.parts:
            continue
        rel = str(path.relative_to(_REPO)).replace("\\", "/")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _calls_emit(tree):
            sites.append(rel)
    expected = ["services/pratibimb/samvaad/verify_service.py"]
    assert sites == expected, (
        f"emit_samvaad_verifier_audit production Call sites drifted: "
        f"expected={expected} got={sites}"
    )


def test_samvaad_c_12_flag_off_still_calls_live_emit_false():
    with patch("services.pratibimb.samvaad.verify_service.SAMVAAD_VERIFIER_LIVE_EMIT", False):
        verify_samvaad({"payload": _valid_summative()})
    assert len(verifier_emits()) == 1
    assert verifier_emits()[0]["params"]["live_emit"] is False
    assert len(summative_ledger_rows()) == 0


def test_samvaad_c_13_flag_on_sink_fail_503():
    class FailingSink:
        def emit(self, _event: object) -> None:
            raise RuntimeError("sink down")

    with patch("services.pratibimb.samvaad.verify_service.SAMVAAD_VERIFIER_LIVE_EMIT", True):
        with pytest.raises(AuditWriteError):
            verify_samvaad(
                {"payload": _valid_summative()},
                sink=FailingSink(),
            )


def test_samvaad_c_14_emit_payload_superset_b_stub_keys():
    stub = emit_samvaad_verifier_audit(outcome="ok", params={"path": "verify_stub"})
    prod_keys = set(verifier_emits()[-1]["params"].keys())
    verify_samvaad({"payload": _valid_summative()})
    prod_keys |= set(verifier_emits()[-1]["params"].keys())
    assert B_STUB_EMIT_PARAM_KEYS <= prod_keys
    assert emit_param_keys_for_matrix() <= prod_keys
    assert stub.caller_kind == "samvaad_verifier"


def test_samvaad_c_15_illegal_transition_distinct_error_kind():
    with pytest.raises(BiasRemediationError) as exc:
        assert_valid_transition("retraining", "removed")
    assert exc.value.error_kind == ERROR_KIND_ILLEGAL_TRANSITION
    rec = capture_summative(_valid_summative())
    insert_summative_evidence(tenant_id="t1", record=rec)
    with pytest.raises(BiasRemediationError) as exc2:
        insert_summative_evidence(tenant_id="t1", record=rec)
    assert exc2.value.error_kind == ERROR_KIND_ILLEGAL_TRANSITION


def test_samvaad_c_16_compose_a_and_b_green():
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "samvaad_acceptance or samvaad_b_acceptance",
        "-q",
        "--tb=no",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
