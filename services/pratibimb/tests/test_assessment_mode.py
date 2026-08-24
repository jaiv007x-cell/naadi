"""
Fail-closed grading and evidence provenance.

A summative session that cannot be graded must raise rather than resolve to a
silent `None`, because downstream a missing grade is indistinguishable from a
pass.
"""
from __future__ import annotations

import pytest

from services.pratibimb.app.case_gen.sampler import CaseSampler
from services.pratibimb.app.eval.ledger import (
    EVIDENCE_KIND_PRIOR_WEIGHT,
    EvidenceCategory,
    EvidenceItem,
    EvidenceKind,
    EvidenceLedger,
    build_ledger,
)
from services.pratibimb.app.eval.nirikshak import (
    MissingGradingBlueprint,
    UngradableSessionError,
)
from services.pratibimb.app.eval.rubric import Axis, GradingBlueprint, RubricHit
from services.pratibimb.app.worker import Session
from shared.schemas.session import AssessmentMode

BLUEPRINT = GradingBlueprint(
    case_id="MODE_TEST",
    case_version="1.0.0",
    rubric_version="0.3.0",
    hits=(
        RubricHit(id="act.aspirin", axis=Axis.ACTION, matcher="drug_given",
                  params={"drug_id": "aspirin"}, points=10),
    ),
)


def _session(mode: AssessmentMode) -> Session:
    case = CaseSampler().sample()
    return Session("sess-1", case, speaker_wav="", assessment_mode=mode)


# ── AssessmentMode ───────────────────────────────────────────────────────────

def test_only_summative_requires_a_grade():
    assert AssessmentMode.SUMMATIVE.requires_grade is True
    assert AssessmentMode.FORMATIVE.requires_grade is False
    assert AssessmentMode.PRACTICE.requires_grade is False


def test_default_mode_is_practice():
    assert _session(AssessmentMode.PRACTICE).assessment_mode is AssessmentMode.PRACTICE
    case = CaseSampler().sample()
    assert Session("s", case, "").assessment_mode is AssessmentMode.PRACTICE


# ── fail-closed finalize ─────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", [AssessmentMode.PRACTICE, AssessmentMode.FORMATIVE])
def test_low_stakes_modes_tolerate_a_missing_blueprint(mode):
    assert _session(mode).finalize() is None


def test_summative_without_a_blueprint_raises():
    session = _session(AssessmentMode.SUMMATIVE)
    with pytest.raises(MissingGradingBlueprint) as exc:
        session.finalize()
    assert exc.value.session_id == "sess-1"
    assert "grading_blueprint" in exc.value.reason


def test_missing_blueprint_is_catchable_as_ungradable():
    """Callers that only care 'was this gradable' should not need the subclass."""
    session = _session(AssessmentMode.SUMMATIVE)
    with pytest.raises(UngradableSessionError):
        session.finalize()
    assert issubclass(MissingGradingBlueprint, UngradableSessionError)


def test_missing_blueprint_message_stamps_case_and_version():
    exc = MissingGradingBlueprint("s1", "CASE_9", "2.1.0")
    assert "CASE_9@2.1.0" in str(exc)
    assert "refusing to silently skip grading" in str(exc)


def test_missing_blueprint_message_without_a_version():
    assert "CASE_9@" not in str(MissingGradingBlueprint("s1", "CASE_9"))


# ── mode resolution between case and session ─────────────────────────────────

def test_strictest_picks_the_highest_stakes_mode():
    assert AssessmentMode.strictest(
        AssessmentMode.PRACTICE, AssessmentMode.SUMMATIVE
    ) is AssessmentMode.SUMMATIVE
    assert AssessmentMode.strictest(
        AssessmentMode.PRACTICE, AssessmentMode.FORMATIVE
    ) is AssessmentMode.FORMATIVE
    assert AssessmentMode.strictest(AssessmentMode.PRACTICE) is AssessmentMode.PRACTICE


def test_strictest_ignores_none_and_falls_back_to_practice():
    assert AssessmentMode.strictest(None) is AssessmentMode.PRACTICE
    assert AssessmentMode.strictest(
        None, AssessmentMode.FORMATIVE
    ) is AssessmentMode.FORMATIVE


def test_mode_ranks_are_ordered():
    assert (AssessmentMode.PRACTICE.rank
            < AssessmentMode.FORMATIVE.rank
            < AssessmentMode.SUMMATIVE.rank)


def test_a_summative_case_cannot_be_downgraded_by_a_practice_launch():
    """The case declares its own stakes; launching it casually must not disarm them."""
    session = _session(AssessmentMode.PRACTICE)

    class SummativeCase:
        case_id = "GOLD_1"
        version = "1.0.0"
        assessment_mode = AssessmentMode.SUMMATIVE
        grading_blueprint = None

    session.case = SummativeCase()
    assert session.effective_assessment_mode is AssessmentMode.SUMMATIVE
    with pytest.raises(MissingGradingBlueprint):
        session.finalize()


def test_effective_mode_defaults_to_the_session_when_the_case_is_silent():
    session = _session(AssessmentMode.FORMATIVE)
    assert not hasattr(session.case, "assessment_mode")
    assert session.effective_assessment_mode is AssessmentMode.FORMATIVE


def test_ungradable_error_names_the_case_and_session():
    session = _session(AssessmentMode.SUMMATIVE)
    with pytest.raises(UngradableSessionError) as exc:
        session.finalize()
    assert exc.value.case_id == session.case.case_id
    assert "sess-1" in str(exc.value)
    assert "summative" in str(exc.value)


def test_summative_with_a_blueprint_grades_normally():
    session = _session(AssessmentMode.SUMMATIVE)
    grade = session.finalize(BLUEPRINT)
    assert grade is not None
    assert grade.case_id == "MODE_TEST"
    assert session.grade is grade


def test_finalize_records_the_trace_migration():
    session = _session(AssessmentMode.SUMMATIVE)
    session.finalize(BLUEPRINT)
    assert session.trace_migration is not None
    assert session.trace_migration.is_lossless


def test_summative_wraps_grader_failure_as_ungradable():
    """An exploding matcher must not silently downgrade to an absent grade."""
    session = _session(AssessmentMode.SUMMATIVE)
    broken = GradingBlueprint(
        case_id="MODE_TEST", case_version="1.0.0", rubric_version="0.3.0",
        hits=(RubricHit(id="bad", axis=Axis.ACTION, matcher="drug_given",
                        params={}, points=5),),  # missing required "drug_id"
    )
    with pytest.raises(UngradableSessionError) as exc:
        session.finalize(broken)
    assert "KeyError" in exc.value.reason


def test_low_stakes_swallows_grader_failure():
    session = _session(AssessmentMode.PRACTICE)
    broken = GradingBlueprint(
        case_id="MODE_TEST", case_version="1.0.0", rubric_version="0.3.0",
        hits=(RubricHit(id="bad", axis=Axis.ACTION, matcher="drug_given",
                        params={}, points=5),),
    )
    assert session.finalize(broken) is None


# ── evidence provenance ──────────────────────────────────────────────────────

def test_evidence_kind_is_separate_from_evaluator_version():
    item = EvidenceItem(
        t=1.0, category=EvidenceCategory.COMMUNICATION, tag="tone",
        kind=EvidenceKind.LLM_JUDGE, evaluator_version="judge-gpt4o-2026-01",
    )
    assert item.kind is EvidenceKind.LLM_JUDGE
    assert item.evaluator_version == "judge-gpt4o-2026-01"


def test_every_kind_has_a_prior_weight():
    for kind in EvidenceKind:
        assert kind in EVIDENCE_KIND_PRIOR_WEIGHT
        assert 0.0 < EVIDENCE_KIND_PRIOR_WEIGHT[kind] <= 1.0


def test_preceptor_outranks_auto_rubric_outranks_self_report():
    w = EVIDENCE_KIND_PRIOR_WEIGHT
    assert w[EvidenceKind.PRECEPTOR] > w[EvidenceKind.AUTO_RUBRIC]
    assert w[EvidenceKind.AUTO_RUBRIC] > w[EvidenceKind.LLM_JUDGE]
    assert w[EvidenceKind.LLM_JUDGE] > w[EvidenceKind.PEER]
    assert w[EvidenceKind.PEER] > w[EvidenceKind.SELF_REPORT]


def test_self_report_is_the_lowest_weight():
    assert EVIDENCE_KIND_PRIOR_WEIGHT[EvidenceKind.SELF_REPORT] == min(
        EVIDENCE_KIND_PRIOR_WEIGHT.values()
    )


def test_ledger_defaults_new_items_to_auto_rubric():
    ledger = EvidenceLedger(evaluator_version="nirikshak-0.3.0")
    ledger.add(1.0, EvidenceCategory.CLINICAL, "ecg_ordered")
    assert ledger.items[0].kind is EvidenceKind.AUTO_RUBRIC
    assert ledger.items[0].evaluator_version == "nirikshak-0.3.0"


def test_ledger_item_can_override_evaluator_version():
    ledger = EvidenceLedger(evaluator_version="nirikshak-0.3.0")
    ledger.add(1.0, EvidenceCategory.COMMUNICATION, "empathy",
               kind=EvidenceKind.LLM_JUDGE, evaluator_version="judge-v2")
    assert ledger.items[0].evaluator_version == "judge-v2"


def test_ledger_queries_by_kind():
    ledger = EvidenceLedger()
    ledger.add(1.0, EvidenceCategory.CLINICAL, "a", kind=EvidenceKind.AUTO_RUBRIC)
    ledger.add(2.0, EvidenceCategory.CLINICAL, "b", kind=EvidenceKind.PRECEPTOR)
    assert len(ledger.items_by_kind(EvidenceKind.PRECEPTOR)) == 1
    assert ledger.kinds_present() == {EvidenceKind.AUTO_RUBRIC, EvidenceKind.PRECEPTOR}


def test_weighted_impact_discounts_by_provenance():
    ledger = EvidenceLedger()
    ledger.add(1.0, EvidenceCategory.CLINICAL, "claimed", score_impact=10.0,
               kind=EvidenceKind.SELF_REPORT)
    ledger.add(2.0, EvidenceCategory.CLINICAL, "observed", score_impact=10.0,
               kind=EvidenceKind.PRECEPTOR)
    assert ledger.total_impact(EvidenceCategory.CLINICAL) == 20.0
    weighted = ledger.weighted_impact(EvidenceCategory.CLINICAL)
    assert weighted == pytest.approx(10.0 * 0.15 + 10.0 * 1.0)
    assert weighted < ledger.total_impact(EvidenceCategory.CLINICAL)


def test_build_ledger_stamps_the_grader_version():
    from services.pratibimb.app.eval.rubric import NIRIKSHAK_VERSION
    from services.pratibimb.app.physio.state import PhysioTrace as LegacyTrace

    case = CaseSampler().sample()
    ledger = build_ledger(case, [], LegacyTrace())
    assert ledger.evaluator_version == f"nirikshak-{NIRIKSHAK_VERSION}"
