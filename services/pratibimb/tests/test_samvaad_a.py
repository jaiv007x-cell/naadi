"""SAMVAAD.a — 16-case acceptance matrix."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.samvaad_acceptance

from services.pratibimb.audit.caller_kinds import (
    KNOWN_AUDIT_CALLER_KINDS,
    assert_known_caller_kind,
)
from services.pratibimb.samvaad.constants import (
    CITATION_STATE_A_ENFORCED,
    DOMAINS,
    PHASE1_PILOT_IMMINENT,
    REFERENCE_RUBRICS_VERSION,
)
from services.pratibimb.samvaad.fail_case_allowlist import FAIL_CASE_ALLOWLIST
from services.pratibimb.samvaad.invariants import (
    BIOMETRIC_GREP_TOKENS,
    FORBIDDEN_PRESENTATION_FEATURES,
    PATIENT_FEEDBACK_GREP_TOKENS,
)
from services.pratibimb.samvaad.matcher_spec import REQUIRED_STEPS
from services.pratibimb.samvaad.reference.v1 import (
    CONTENT_HASH,
    assert_domains_complete,
    assert_fail_cases_allowlisted,
    domains_present,
    fail_case_competencies,
    reference_content_hash,
    reference_rubrics_v1,
)
from services.pratibimb.samvaad.schema import (
    SamvaadSchemaError,
    validate_all_reference_rubrics,
    validate_reference_rubric,
    validate_summative_payload,
)

_REPO = Path(__file__).resolve().parents[3]
_SAMVAAD = _REPO / "services" / "pratibimb" / "samvaad"
_CREDENTIALS = _REPO / "services" / "pratibimb" / "credentials"
_BEEMA = _REPO / "services" / "beema"
_BEEMA_APP = _REPO / "services" / "pratibimb" / "app" / "beema"
_MIGRATION_018 = (
    _REPO
    / "services"
    / "pratibimb"
    / "ledger"
    / "migrations"
    / "018_samvaad_verifier_caller_kind.sql"
)


def _iter_py(root: Path):
    if not root.exists():
        return
    yield from root.rglob("*.py")


def _text_has_token(text: str, token: str) -> bool:
    """Identifier-aware match — not raw substring (avoids ``is_patient_feedback_field``)."""
    if token.endswith("_"):
        return bool(re.search(re.escape(token) + r"\w+", text))
    # Whole identifier or dotted module path
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text))


# ── 1–3 reference rubrics ────────────────────────────────────────────────────


def test_samvaad_a_1_nine_domains_present():
    assert_domains_complete()
    assert domains_present() == DOMAINS
    rubrics = reference_rubrics_v1()
    assert len(rubrics) >= 9
    assert REFERENCE_RUBRICS_VERSION == "samvaad.reference_rubrics.v1.1"


def test_samvaad_a_2_content_hash_stable():
    assert reference_content_hash() == CONTENT_HASH
    assert len(CONTENT_HASH) == 64
    # Immutability: recomputed hash equals pinned constant (v1 bytes frozen)
    again = reference_content_hash()
    assert again == CONTENT_HASH


def test_samvaad_a_3_all_reference_rubrics_validate():
    validate_all_reference_rubrics(reference_rubrics_v1())


# ── 4–6 schema rejects ───────────────────────────────────────────────────────


def test_samvaad_a_4_subjective_scale_rejected():
    with pytest.raises(SamvaadSchemaError, match="I-S-2"):
        validate_summative_payload({"attitude": 7})
    with pytest.raises(SamvaadSchemaError, match="I-S-2"):
        validate_summative_payload({"personality_openness": 0.5})
    with pytest.raises(SamvaadSchemaError, match="I-S-2"):
        validate_summative_payload({"evidence": {"professionalism_score": 9}})


def test_samvaad_a_5_biometric_rejected():
    with pytest.raises(SamvaadSchemaError, match="I-S-9"):
        validate_summative_payload({"heart_rate": 88})
    with pytest.raises(SamvaadSchemaError, match="I-S-9"):
        validate_summative_payload({"biometric_stress_index": 0.2})
    with pytest.raises(SamvaadSchemaError, match="I-S-9"):
        validate_summative_payload({"facial_emotion_label": "stressed"})


def test_samvaad_a_6_patient_feedback_rejected():
    with pytest.raises(SamvaadSchemaError, match="I-S-13"):
        validate_summative_payload({"patient_feedback_score": 5})
    with pytest.raises(SamvaadSchemaError, match="I-S-13"):
        validate_reference_rubric(
            {
                "domain": "A",
                "competency": "comm.patient.register_match",
                "matcher": {"kind": "register_appropriateness"},
                "params": {"patient_rating": 4},
                "required": True,
                "points": 1,
                "fail_case_on_violation": False,
            }
        )


# ── 7–9 grep / import-graph belts ────────────────────────────────────────────


def test_samvaad_a_7_biometric_grep_credentials_beema():
    roots = (_CREDENTIALS, _BEEMA, _BEEMA_APP)
    offenders: list[str] = []
    for root in roots:
        for path in _iter_py(root):
            text = path.read_text(encoding="utf-8")
            for token in BIOMETRIC_GREP_TOKENS:
                if token == "samvaad.biometric_summative":
                    continue
                if _text_has_token(text, token):
                    offenders.append(f"{path.relative_to(_REPO)}:{token}")
    assert offenders == []


def test_samvaad_a_8_patient_feedback_grep_samvaad_credentials():
    # Summative paths under samvaad (exclude this test file's string literals via
    # checking production package only) + credentials.
    roots = (_SAMVAAD, _CREDENTIALS)
    offenders: list[str] = []
    for root in roots:
        for path in _iter_py(root):
            # invariants.py *defines* the deny-list tokens — allowed.
            if path.name == "invariants.py":
                continue
            text = path.read_text(encoding="utf-8")
            for token in PATIENT_FEEDBACK_GREP_TOKENS:
                if _text_has_token(text, token):
                    offenders.append(f"{path.relative_to(_REPO)}:{token}")
    assert offenders == []


def test_samvaad_a_9_import_graph_no_biometric_summative():
    bio_mod = _SAMVAAD / "biometric_summative.py"
    bio_pkg = _SAMVAAD / "biometric_summative"
    assert not bio_mod.exists()
    assert not bio_pkg.exists()

    for root in (_CREDENTIALS, _BEEMA, _BEEMA_APP):
        for path in _iter_py(root):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    parts = node.module.split(".")
                    assert "biometric" not in parts, path
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "biometric" not in alias.name.split("."), path


# ── 10–11 caller_kind + migration 018 ────────────────────────────────────────


def test_samvaad_a_10_caller_kind_enum():
    assert "samvaad_verifier" in KNOWN_AUDIT_CALLER_KINDS
    assert_known_caller_kind("samvaad_verifier")
    with pytest.raises(ValueError, match="KNOWN_AUDIT_CALLER_KINDS"):
        assert_known_caller_kind("samvaad-verifier")
    with pytest.raises(ValueError, match="KNOWN_AUDIT_CALLER_KINDS"):
        assert_known_caller_kind("samvaad_evidence_audit")


def test_samvaad_a_11_migration_018_belt():
    sql = _MIGRATION_018.read_text(encoding="utf-8")
    assert "samvaad_verifier" in sql
    assert "ck_audit_caller_kind" in sql
    assert "ncvet_regrader" in sql  # prior kinds retained


# ── 12–14 rubrics / matcher / presentation ───────────────────────────────────


def test_samvaad_a_12_fail_case_allowlist():
    assert_fail_cases_allowlisted()
    assert fail_case_competencies() <= FAIL_CASE_ALLOWLIST
    # Allowlist itself is non-empty and includes safety-critical pins
    assert "comm.handoff.sbar_complete" in FAIL_CASE_ALLOWLIST
    assert "digi.patient_id.two_source_verify" in FAIL_CASE_ALLOWLIST


def test_samvaad_a_13_empathy_matcher_spec_steps():
    from services.pratibimb.samvaad.matcher_spec import COMBINATOR, empathy_hit_fires

    empathy = next(
        r
        for r in reference_rubrics_v1()
        if r["competency"] == "comm.empathy.acknowledge_distress"
    )
    required = empathy["matcher"]["required"]
    for step in REQUIRED_STEPS:
        assert step in required
    assert empathy["matcher"]["kind"] == "action_sequence"
    assert empathy["params"]["combinator"] == COMBINATOR
    assert "supporting_cue_phrases" in empathy["params"]
    # AND: all true → hit; one false → miss; interruption veto → miss
    all_true = {s: True for s in REQUIRED_STEPS}
    assert empathy_hit_fires(all_true, interruptions_after_ack=0) is True
    partial = {**all_true, "teach_back_or_check_understanding": False}
    assert empathy_hit_fires(partial, interruptions_after_ack=0) is False
    assert empathy_hit_fires(all_true, interruptions_after_ack=2) is False


def test_samvaad_a_14_domain_g_forbidden_and_a_register():
    g = next(r for r in reference_rubrics_v1() if r["domain"] == "G")
    assert FORBIDDEN_PRESENTATION_FEATURES.issubset(set(g["forbidden_features"]))
    a = next(
        r for r in reference_rubrics_v1() if r["competency"] == "comm.patient.register_match"
    )
    assert a["params"]["forbid_language_detection_as_sole_pass"] is True
    assert a["matcher"]["kind"] != "language_detected"


# ── 15–16 inversion + citation + no parallel audit ────────────────────────────


def test_samvaad_a_15_pilot_not_imminent_no_parallel_audit():
    assert PHASE1_PILOT_IMMINENT is False
    parallel = _SAMVAAD / "samvaad_evidence_audit.py"
    assert not parallel.exists()
    for path in _iter_py(_SAMVAAD):
        text = path.read_text(encoding="utf-8")
        # Allow named-exclusion prose ("No parallel samvaad_evidence_audit table")
        if "no parallel" in text.lower() and "samvaad_evidence_audit" in text:
            continue
        assert "samvaad_evidence_audit" not in text or path.name.startswith("test_")


def test_samvaad_a_16_citation_state():
    from services.pratibimb.samvaad.bias_remediation import (
        ALLOWED_TRANSITIONS,
        STATE_ACTIVE,
        STATE_DOWN_WEIGHTED,
        STATE_REMOVED,
        STATE_RETRAINING,
        STATE_UNDER_REVIEW,
    )
    from services.pratibimb.samvaad.evidence_class import (
        KNOWN_EVIDENCE_CLASSES,
        summative_eligible,
    )
    from services.pratibimb.samvaad.summative_contract import (
        DHAARA_DECAY_CRON_TIMEZONE,
        DHAARA_FRESHNESS_TRIGGER,
        REQUIRED_SUMMATIVE_FIELDS,
    )

    assert "invariants I-S-1…15 frozen" in CITATION_STATE_A_ENFORCED
    assert KNOWN_EVIDENCE_CLASSES == frozenset(
        {"machine_sim", "preceptor_attested", "patient_reported"}
    )
    assert summative_eligible("machine_sim") is True
    assert summative_eligible("patient_reported") is False
    assert "transcript_digest" in REQUIRED_SUMMATIVE_FIELDS
    assert DHAARA_FRESHNESS_TRIGGER == "event_on_evidence + daily_decay_cron"
    assert DHAARA_DECAY_CRON_TIMEZONE == "UTC"
    assert (STATE_ACTIVE, STATE_DOWN_WEIGHTED) in ALLOWED_TRANSITIONS
    assert (STATE_RETRAINING, STATE_ACTIVE) in ALLOWED_TRANSITIONS
    assert (STATE_UNDER_REVIEW, STATE_REMOVED) in ALLOWED_TRANSITIONS
    assert (STATE_REMOVED, STATE_ACTIVE) not in ALLOWED_TRANSITIONS


def test_samvaad_a_17_domain_ordinals_stable():
    from services.pratibimb.samvaad.constants import (
        DOMAIN_ORDINALS_V1,
        assert_domain_ordinal_stable,
        next_additive_domain_letter,
    )

    assert_domain_ordinal_stable()
    assert DOMAIN_ORDINALS_V1 == ("A", "B", "C", "D", "E", "F", "G", "H", "I")
    assert next_additive_domain_letter(DOMAIN_ORDINALS_V1) == "J"


def test_samvaad_a_18_interruption_veto_window_pinned():
    from services.pratibimb.samvaad.matcher_spec import (
        EMPATHY_CUE_WINDOW_MS,
        INTERRUPTION_VETO,
    )

    assert EMPATHY_CUE_WINDOW_MS == 5000
    assert INTERRUPTION_VETO["window_ms_from_empathy_cue_onset"] == 5000
    assert 2000 <= EMPATHY_CUE_WINDOW_MS <= 5000
    empathy = next(
        r
        for r in reference_rubrics_v1()
        if r["competency"] == "comm.empathy.acknowledge_distress"
    )
    assert empathy["params"]["empathy_cue_window_ms"] == 5000


def test_samvaad_a_19_patient_reported_summative_fail_closed():
    from services.pratibimb.samvaad.evidence_class import (
        EvidenceClass,
        assert_summative_evidence_class,
    )
    from services.pratibimb.samvaad.summative_contract import SummativeEvidenceError

    assert_summative_evidence_class(EvidenceClass.MACHINE_SIM.value)
    assert_summative_evidence_class(EvidenceClass.PRECEPTOR_ATTESTED.value)
    with pytest.raises(SummativeEvidenceError, match="I-S-13"):
        assert_summative_evidence_class(EvidenceClass.PATIENT_REPORTED.value)


def test_samvaad_a_20_citation_anchor_in_fixture_export():
    from services.pratibimb.samvaad.fixtures import framework_acceptance_fixture

    fixture = framework_acceptance_fixture()
    assert fixture["framework_citation_anchor"] == CITATION_STATE_A_ENFORCED
    assert "SAMVAAD.a enforced" in fixture["framework_citation_anchor"]
    assert "invariants I-S-1…15 frozen" in fixture["framework_citation_anchor"]
    dumped = str(fixture)
    assert CITATION_STATE_A_ENFORCED in dumped


def test_samvaad_a_21_domain_ordinals_runtime_immutable():
    """I-S-1 plan-gate: tuple item-assign raises; module constant not mutated by rebind."""
    import services.pratibimb.samvaad.constants as c

    from services.pratibimb.samvaad.constants import (
        DOMAIN_ORDINALS_V1,
        next_additive_domain_letter,
    )

    assert isinstance(DOMAIN_ORDINALS_V1, tuple)
    assert isinstance(c.DOMAIN_ORDINALS_V1, tuple)
    with pytest.raises(TypeError):
        c.DOMAIN_ORDINALS_V1[0] = "Z"  # type: ignore[index]
    # Local rebind must not mutate the module constant
    before = c.DOMAIN_ORDINALS_V1
    local = c.DOMAIN_ORDINALS_V1 + ("J",)
    assert local[-1] == "J"
    assert c.DOMAIN_ORDINALS_V1 is before
    assert c.DOMAIN_ORDINALS_V1 == ("A", "B", "C", "D", "E", "F", "G", "H", "I")
    assert next_additive_domain_letter(DOMAIN_ORDINALS_V1) == "J"


def test_samvaad_a_22_bias_illegal_transitions_rejected():
    """I-S-11 negative space: cannot skip rungs or revive removed attest authority."""
    from services.pratibimb.samvaad.bias_remediation import (
        ERROR_KIND_ILLEGAL_TRANSITION,
        BiasRemediationError,
        STATE_ACTIVE,
        STATE_DOWN_WEIGHTED,
        STATE_REMOVED,
        STATE_RETRAINING,
        STATE_UNDER_REVIEW,
        assert_valid_transition,
    )

    # Legal
    assert_valid_transition(STATE_ACTIVE, STATE_DOWN_WEIGHTED)
    assert_valid_transition(STATE_DOWN_WEIGHTED, STATE_UNDER_REVIEW)
    assert_valid_transition(STATE_UNDER_REVIEW, STATE_RETRAINING)
    assert_valid_transition(STATE_UNDER_REVIEW, STATE_REMOVED)
    assert_valid_transition(STATE_RETRAINING, STATE_ACTIVE)
    assert_valid_transition(STATE_RETRAINING, STATE_UNDER_REVIEW)

    # Illegal — skip rungs / revive terminal / exit from recovery path
    for src, dst in (
        (STATE_DOWN_WEIGHTED, STATE_REMOVED),
        (STATE_ACTIVE, STATE_REMOVED),
        (STATE_DOWN_WEIGHTED, STATE_RETRAINING),
        (STATE_REMOVED, STATE_ACTIVE),
        (STATE_ACTIVE, STATE_UNDER_REVIEW),
        (STATE_RETRAINING, STATE_REMOVED),
    ):
        with pytest.raises(BiasRemediationError) as exc:
            assert_valid_transition(src, dst)
        assert exc.value.error_kind == ERROR_KIND_ILLEGAL_TRANSITION
        assert exc.value.from_state == src
        assert exc.value.to_state == dst
