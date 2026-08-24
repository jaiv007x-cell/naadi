"""SAMVAAD.b — 14-case acceptance matrix.

Framework-layer changes (AND-gate, ordinal set) = stop-and-re-countersign.
Rubric-param tunes stay in-slice.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.samvaad_b_acceptance

from services.pratibimb.samvaad.bias_remediation import (
    STATE_ACTIVE,
    STATE_DOWN_WEIGHTED,
    STATE_REMOVED,
    STATE_RETRAINING,
    STATE_UNDER_REVIEW,
    assert_valid_transition,
)
from services.pratibimb.samvaad.constants import (
    CITATION_STATE_A_ENFORCED,
    DOMAIN_ORDINALS_V1,
    DOMAIN_SET_VERSION,
    PHASE1_PILOT_IMMINENT,
)
from services.pratibimb.samvaad.corpus import SamvaadCorpus, load_phase1_digital_literacy
from services.pratibimb.samvaad.evidence_class import EvidenceClass
from services.pratibimb.samvaad.fixtures import framework_acceptance_fixture
from services.pratibimb.samvaad.matcher_spec import (
    COMBINATOR,
    EMPATHY_CUE_WINDOW_MS,
    REQUIRED_STEPS,
)
from services.pratibimb.samvaad.runtime import (
    evaluate_action_sequence,
    evaluate_empathy_and,
    rubric_window_parity,
)
from services.pratibimb.samvaad.summative import (
    capture_summative,
    clear_summative_store,
    rcp_shaped_fields,
)
from services.pratibimb.samvaad.summative_contract import SummativeEvidenceError
from services.pratibimb.samvaad.verifier_emit import (
    clear_verifier_emits,
    emit_samvaad_verifier_audit,
    verifier_emits,
)
from services.pratibimb.samvaad.versioning import (
    assert_and_gate_change_requires_framework,
    assert_window_tune_stays_on_same_framework,
    is_framework_change,
    is_rubric_param_change,
)

_REPO = Path(__file__).resolve().parents[3]
_SAMVAAD = _REPO / "services" / "pratibimb" / "samvaad"


@pytest.fixture(autouse=True)
def _clean_stores():
    clear_summative_store()
    clear_verifier_emits()
    yield
    clear_summative_store()
    clear_verifier_emits()


def test_samvaad_b_1_corpus_nine_domains_ordinals():
    corpus = SamvaadCorpus()
    corpus.assert_ordinals_stable()
    assert corpus.domains_present() == frozenset(DOMAIN_ORDINALS_V1)
    assert len(corpus.all()) >= 9


def test_samvaad_b_2_domain_ordinals_immutable():
    import services.pratibimb.samvaad.constants as c

    with pytest.raises(TypeError):
        c.DOMAIN_ORDINALS_V1[0] = "Z"  # type: ignore[index]
    before = c.DOMAIN_ORDINALS_V1
    _ = c.DOMAIN_ORDINALS_V1 + ("J",)
    assert c.DOMAIN_ORDINALS_V1 is before


def test_samvaad_b_3_empathy_runtime_and_window():
    steps = {s: True for s in REQUIRED_STEPS}
    ok, ev = evaluate_empathy_and(steps, interruptions_after_ack=0)
    assert ok is True
    assert ev["combinator"] == COMBINATOR
    assert ev["window_ms"] == EMPATHY_CUE_WINDOW_MS

    # Interruption inside window vetoes
    ok2, _ = evaluate_empathy_and(
        steps,
        interruptions_after_ack=0,
        interruption_timestamps_ms=[1000, 2000],
        cue_onset_ms=0,
        window_ms=5000,
    )
    assert ok2 is False

    # Interruptions outside window do not veto
    ok3, _ = evaluate_empathy_and(
        steps,
        interruptions_after_ack=0,
        interruption_timestamps_ms=[6000, 7000],
        cue_onset_ms=0,
        window_ms=5000,
    )
    assert ok3 is True


def test_samvaad_b_4_rubric_matcher_window_parity():
    empathy = next(
        r
        for r in SamvaadCorpus().all()
        if r["competency"] == "comm.empathy.acknowledge_distress"
    )
    assert rubric_window_parity(empathy) is True
    assert empathy["params"]["empathy_cue_window_ms"] == EMPATHY_CUE_WINDOW_MS


def test_samvaad_b_5_digital_literacy_compile_dry_run():
    from services.pratibimb.samvaad.schema import validate_reference_rubric

    h = load_phase1_digital_literacy()
    assert len(h) >= 2
    for r in h:
        validate_reference_rubric(r)
        assert r["domain"] == "H"
    # Runtime action_sequence for patient-ID
    pid = next(r for r in h if r["competency"] == "digi.patient_id.two_source_verify")
    required = pid["matcher"]["required"]
    ok, _ = evaluate_action_sequence(
        ["scan_wristband", "confirm_patient_on_screen", "draw"],
        required,
    )
    assert ok is True
    miss, _ = evaluate_action_sequence(["scan_wristband"], required)
    assert miss is False


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


def test_samvaad_b_6_summative_rejects_missing_digest_fields():
    with pytest.raises(SummativeEvidenceError, match="I-S-14"):
        capture_summative(
            {
                "grader_version": "g",
                "rubric_schema_version": "r",
                "artifact_schema_version": "a",
                "evidence_class": EvidenceClass.MACHINE_SIM.value,
            }
        )


def test_samvaad_b_7_summative_rejects_patient_reported():
    with pytest.raises(SummativeEvidenceError, match="I-S-13"):
        capture_summative(
            _valid_summative(evidence_class=EvidenceClass.PATIENT_REPORTED.value)
        )


def test_samvaad_b_8_summative_accepts_machine_and_preceptor():
    r1 = capture_summative(_valid_summative())
    r2 = capture_summative(
        _valid_summative(evidence_class=EvidenceClass.PRECEPTOR_ATTESTED.value)
    )
    assert r1.evidence_class == "machine_sim"
    assert r2.evidence_class == "preceptor_attested"


def test_samvaad_b_9_bias_legal_and_illegal():
    from services.pratibimb.samvaad.bias_remediation import (
        ERROR_KIND_ILLEGAL_TRANSITION,
        BiasRemediationError,
    )

    assert_valid_transition(STATE_ACTIVE, STATE_DOWN_WEIGHTED)
    assert_valid_transition(STATE_UNDER_REVIEW, STATE_REMOVED)
    assert_valid_transition(STATE_RETRAINING, STATE_UNDER_REVIEW)
    for src, dst in (
        (STATE_DOWN_WEIGHTED, STATE_REMOVED),
        (STATE_ACTIVE, STATE_REMOVED),
        (STATE_RETRAINING, STATE_REMOVED),
        (STATE_REMOVED, STATE_ACTIVE),
    ):
        with pytest.raises(BiasRemediationError) as exc:
            assert_valid_transition(src, dst)
        assert exc.value.error_kind == ERROR_KIND_ILLEGAL_TRANSITION


def test_samvaad_b_10_rcp_shaped_fields():
    rec = capture_summative(_valid_summative())
    fields = rcp_shaped_fields(rec)
    assert "transcript_digest" in fields
    assert "grader_version" in fields
    assert "rubric_schema_version" in fields
    assert "artifact_schema_version" in fields
    d = rec.to_dict()
    assert d["framework_citation_anchor"] == CITATION_STATE_A_ENFORCED


def test_samvaad_b_11_no_parallel_audit_table():
    assert not (_SAMVAAD / "samvaad_evidence_audit.py").exists()
    for path in _SAMVAAD.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        assert "samvaad_evidence_audit" not in text or "no parallel" in text.lower()


def test_samvaad_b_12_samvaad_verifier_emit():
    """Stub callable from tests; production tree has zero emit Call sites (.b gate).

    AST-based (not substring): aliases and string literals cannot defeat / false-positive.
    """
    import ast

    ev = emit_samvaad_verifier_audit(outcome="ok", params={"path": "verify_stub"})
    assert ev.caller_kind == "samvaad_verifier"
    assert len(verifier_emits()) == 1
    assert verifier_emits()[0]["caller_kind"] == "samvaad_verifier"

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

    offenders: list[str] = []
    allowed = {"services/pratibimb/samvaad/verify_service.py"}
    for path in (_REPO / "services" / "pratibimb").rglob("*.py"):
        if path.name == "verifier_emit.py":
            continue
        if "tests" in path.parts:
            continue
        rel = str(path.relative_to(_REPO)).replace("\\", "/")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _calls_emit(tree):
            if rel not in allowed:
                offenders.append(rel)
    assert offenders == [], f"unexpected samvaad_verifier emit Call sites: {offenders}"


def test_samvaad_b_13_citation_anchor_fixture():
    fix = framework_acceptance_fixture()
    assert fix["framework_citation_anchor"] == CITATION_STATE_A_ENFORCED
    assert PHASE1_PILOT_IMMINENT is False


def test_samvaad_b_14_framework_vs_rubric_layer_split():
    """
    Additive-only ordinals: append J under Framework bump; never reorder/remove
    without Framework bump. Window tune = rubric only, same DOMAIN_SET_VERSION.
    """
    assert is_rubric_param_change("tune_empathy_window_ms")
    assert not is_framework_change("tune_empathy_window_ms")
    assert_window_tune_stays_on_same_framework(
        old_window_ms=5000,
        new_window_ms=4000,
        framework_version=DOMAIN_SET_VERSION,
    )
    assert is_framework_change("and_to_or")
    assert is_framework_change("remove_interruption_veto")
    assert is_framework_change("reorder_domains")
    assert_and_gate_change_requires_framework("and_to_or")
    # Symmetry: structural AND still required under Framework v1
    assert COMBINATOR == "multi_signal_and"
    assert DOMAIN_ORDINALS_V1 == ("A", "B", "C", "D", "E", "F", "G", "H", "I")
