"""CaseBlueprintV2 contract: content hashing, validation, tiering."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from shared.schemas.case import Demographics, Language, Vital
from shared.schemas.case_v2 import (
    CASE_SCHEMA_VERSION,
    AssessmentMode,
    CaseBlueprintV2,
    CaseIdentity,
    CaseTargeting,
    ClinicalTruth,
    CorpusTier,
    EnvironmentSpec,
    InteractionSpec,
    PatientPersona,
    PhysiologySpec,
    Provenance,
)
from shared.schemas.grading import Axis, GradingBlueprint, RubricHit

DEMO = Demographics(
    name="Ramesh Kale", age=47, sex="M", occupation="auto-rickshaw driver",
    state="Maharashtra", city="Nagpur", native_language=Language.MARATHI,
    education_years=5, monthly_income_inr=14000,
)
VITALS = Vital(hr=104, sbp=148, dbp=92, spo2=96, rr=20, temp_c=36.9)

BLUEPRINT = GradingBlueprint(
    case_id="C1", case_version="1.0.0", rubric_version="0.3.0",
    hits=(RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                    params={"drug_id": "aspirin"}, points=10),),
)


def _case(**overrides) -> CaseBlueprintV2:
    base = dict(
        identity=CaseIdentity(
            case_id="C1", version="1.0.0", title="Inferior wall STEMI",
            corpus_tier=CorpusTier.GOLD, assessment_mode=AssessmentMode.PRACTICE,
        ),
        targeting=CaseTargeting(
            roles=("gnm",), competencies=("cardiac.stemi",), difficulty=0.55,
        ),
        patient=PatientPersona(
            demographics=DEMO, language=Language.MARATHI,
            voice_profile="ramesh_kale_mr", literacy_years=5,
        ),
        clinical_truth=ClinicalTruth(
            primary_diagnosis="Inferior wall STEMI", icd10="I21.19",
            differentials=("GERD",), symptoms_present=("chest pain",),
            findings_present=("diaphoresis",), absent_findings=("fever",),
            allergies=(), medications=("metformin",), onset_minutes_ago=40,
        ),
        environment=EnvironmentSpec(setting="opd", resources=("ecg",)),
        physiology=PhysiologySpec(engine_version="0.2.0", initial_vitals=VITALS),
        interaction=InteractionSpec(
            allowed_disclosures=("chest pain",), hidden_facts=("T2DM 8y",),
        ),
        grading_blueprint=BLUEPRINT,
        provenance=Provenance(
            author="dr_a", clinical_reviewer="dr_b", sources=("AHA 2025",),
            guideline_versions=("AHA-2025.1",),
            reviewed_at=datetime(2026, 1, 1), content_hash="",
        ),
    )
    base.update(overrides)
    return CaseBlueprintV2(**base)


# ── accessors ────────────────────────────────────────────────────────────────

def test_convenience_accessors_delegate_to_identity():
    case = _case()
    assert case.case_id == "C1"
    assert case.version == "1.0.0"
    assert case.assessment_mode is AssessmentMode.PRACTICE
    assert case.corpus_tier is CorpusTier.GOLD
    assert case.is_gradable is True


def test_case_without_blueprint_is_not_gradable():
    assert _case(grading_blueprint=None).is_gradable is False


def test_schema_version_is_stamped():
    assert _case().schema_version == CASE_SCHEMA_VERSION


# ── content hash ─────────────────────────────────────────────────────────────

def test_content_hash_is_deterministic():
    assert _case().compute_content_hash() == _case().compute_content_hash()


def test_content_hash_is_sha256_hex():
    assert len(_case().compute_content_hash()) == 64


def test_content_hash_changes_with_clinical_truth():
    a = _case()
    b = _case(clinical_truth=replace(a.clinical_truth, icd10="I21.20"))
    assert a.compute_content_hash() != b.compute_content_hash()


def test_content_hash_changes_with_grading_blueprint():
    a = _case()
    harder = replace(
        BLUEPRINT,
        hits=(RubricHit(id="a", axis=Axis.ACTION, matcher="drug_given",
                        params={"drug_id": "aspirin"}, points=99),),
    )
    assert a.compute_content_hash() != _case(grading_blueprint=harder).compute_content_hash()


def test_content_hash_ignores_provenance():
    """Re-review of unchanged content must not invalidate the hash."""
    a = _case()
    reattributed = _case(provenance=replace(
        a.provenance, author="dr_c", clinical_reviewer="dr_d",
        reviewed_at=datetime(2030, 5, 5), sources=("different",),
    ))
    assert a.compute_content_hash() == reattributed.compute_content_hash()


def test_content_hash_ignores_recorded_hash_field():
    a = _case()
    assert a.compute_content_hash() == _case(
        provenance=replace(a.provenance, content_hash="stale-value")
    ).compute_content_hash()


def test_with_content_hash_records_and_verifies():
    case = _case().with_content_hash()
    assert case.provenance.content_hash == case.compute_content_hash()
    assert case.content_hash_matches is True


def test_content_hash_mismatch_is_detected():
    case = _case().with_content_hash()
    tampered = replace(
        case, clinical_truth=replace(case.clinical_truth, icd10="TAMPERED")
    )
    assert tampered.content_hash_matches is False


def test_pydantic_submodels_are_canonicalized_not_repr():
    """Demographics/Vital are pydantic; asdict would leave objects that hash by repr."""
    a = _case()
    b = _case(patient=replace(a.patient, demographics=replace_demo(age=48)))
    assert a.compute_content_hash() != b.compute_content_hash()


def replace_demo(**kw) -> Demographics:
    return DEMO.model_copy(update=kw)


def test_dict_field_order_does_not_affect_hash():
    a = _case(environment=EnvironmentSpec(
        setting="opd", resources=("ecg",),
        difficulty_parameters={"a": 1, "b": 2},
    ))
    b = _case(environment=EnvironmentSpec(
        setting="opd", resources=("ecg",),
        difficulty_parameters={"b": 2, "a": 1},
    ))
    assert a.compute_content_hash() == b.compute_content_hash()


def test_to_dict_is_json_serializable():
    import json

    blob = json.dumps(_case().with_content_hash().to_dict())
    assert "Ramesh Kale" in blob
    assert "I21.19" in blob


# ── validation ───────────────────────────────────────────────────────────────

def test_well_formed_practice_case_is_valid():
    assert _case().is_valid is True
    assert _case().validation_errors() == []


def test_empty_case_id_is_an_error():
    case = _case()
    broken = replace(case, identity=replace(case.identity, case_id=""))
    assert "identity.case_id is empty" in broken.validation_errors()


def test_difficulty_out_of_range_is_an_error():
    case = _case()
    broken = replace(case, targeting=replace(case.targeting, difficulty=1.7))
    assert any("difficulty" in e for e in broken.validation_errors())


def test_summative_requires_a_grading_blueprint():
    case = _case(grading_blueprint=None)
    summative = replace(
        case, identity=replace(case.identity, assessment_mode=AssessmentMode.SUMMATIVE)
    )
    assert "summative case has no grading_blueprint" in summative.validation_errors()
    assert summative.is_valid is False


def test_summative_requires_a_clinical_reviewer():
    case = _case()
    summative = replace(
        case,
        identity=replace(case.identity, assessment_mode=AssessmentMode.SUMMATIVE),
        provenance=replace(case.provenance, clinical_reviewer=None),
    )
    assert "summative case has no clinical reviewer" in summative.validation_errors()


def test_summative_requires_gold_tier():
    case = _case()
    summative = replace(
        case,
        identity=replace(
            case.identity,
            assessment_mode=AssessmentMode.SUMMATIVE,
            corpus_tier=CorpusTier.BRONZE,
        ),
    )
    assert any("not gold" in e for e in summative.validation_errors())


def test_fully_provenanced_gold_summative_case_is_valid():
    case = _case()
    summative = replace(
        case, identity=replace(case.identity, assessment_mode=AssessmentMode.SUMMATIVE)
    )
    assert summative.validation_errors() == []


def test_practice_case_tolerates_thin_provenance():
    case = _case(grading_blueprint=None)
    thin = replace(case, provenance=replace(
        case.provenance, clinical_reviewer=None, reviewed_at=None
    ))
    assert thin.is_valid is True


# ── tiering ──────────────────────────────────────────────────────────────────

def test_only_gold_is_summative_eligible():
    assert CorpusTier.GOLD.summative_eligible is True
    for tier in (CorpusTier.SILVER, CorpusTier.BRONZE, CorpusTier.DRAFT):
        assert tier.summative_eligible is False


def test_provenance_review_flag_needs_both_reviewer_and_date():
    p = Provenance("a", "dr_b", (), (), datetime(2026, 1, 1), "")
    assert p.is_clinically_reviewed is True
    assert replace(p, clinical_reviewer=None).is_clinically_reviewed is False
    assert replace(p, reviewed_at=None).is_clinically_reviewed is False


# ── grading blueprint canonical form ─────────────────────────────────────────

def test_blueprint_canonical_dict_is_stable():
    import json

    a = json.dumps(BLUEPRINT.canonical_dict(), sort_keys=True)
    b = json.dumps(BLUEPRINT.canonical_dict(), sort_keys=True)
    assert a == b


def test_blueprint_canonical_dict_sorts_nested_params():
    h1 = RubricHit(id="x", axis=Axis.ACTION, matcher="m",
                   params={"b": 1, "a": 2}, points=1)
    h2 = RubricHit(id="x", axis=Axis.ACTION, matcher="m",
                   params={"a": 2, "b": 1}, points=1)
    assert h1.canonical_dict() == h2.canonical_dict()


def test_blueprint_canonical_dict_captures_rule_content():
    a = BLUEPRINT.canonical_dict()
    b = replace(BLUEPRINT, pass_threshold=0.95).canonical_dict()
    assert a != b
