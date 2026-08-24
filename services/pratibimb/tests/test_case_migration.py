"""v1 -> v2 case migration: tiering, inferred/defaulted split, obsolete actions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.migrate_cases_v1_to_v2 import (
    CorpusMigrationReport,
    classify_tier,
    load_corpus,
    migrate_case_v1_to_v2,
    run_migration,
)
from services.pratibimb.app.action_registry import (
    DEPRECATED_ACTION_IDS,
    KNOWN_ACTION_IDS,
    ActionCategory,
    category_of,
    is_known,
    resolve,
)
from shared.schemas.case_v2 import AssessmentMode, CorpusTier

SEED_CORPUS = (
    Path(__file__).resolve().parents[1]
    / "app" / "case_gen" / "seed_corpus.json"
)


def _v1(**overrides) -> dict:
    base = {
        "case_id": "PRB-001",
        "difficulty": 0.55,
        "competency_tags": ["cardiac.stemi"],
        "demographics": {
            "name": "Ramesh Kale", "age": 47, "sex": "M",
            "occupation": "driver", "state": "Maharashtra", "city": "Nagpur",
            "native_language": "mr", "education_years": 5,
            "monthly_income_inr": 14000,
        },
        "chief_complaint_verbatim": {"mr": "chatit jad watatay"},
        "baseline_vitals": {"hr": 104, "sbp": 148, "dbp": 92,
                            "spo2": 96, "rr": 20, "temp_c": 36.9},
        "hidden": {
            "primary_diagnosis": "Inferior wall STEMI",
            "icd10": "I21.19",
            "onset_minutes_ago": 40,
            "symptoms_present": ["chest pain"],
            "symptoms_absent": ["fever"],
            "comorbidities": ["T2DM 8y"],
            "current_meds": ["metformin"],
            "allergies": [],
            "social_history": {"tobacco": "gutka"},
            "red_herrings": ["blames gas"],
        },
        "expected_actions": ["order_ecg_within_10min", "give_aspirin_325_chewed"],
        "critical_actions": ["order_ecg_within_10min"],
        "time_pressure_seconds": 900,
        "family_present": True,
        "resources_available": ["ecg"],
        "persona_voice_id": "ramesh_kale_mr",
    }
    base.update(overrides)
    return base


# ── action registry ──────────────────────────────────────────────────────────

def test_known_action_ids_cover_the_seed_corpus():
    cases = json.loads(SEED_CORPUS.read_text(encoding="utf-8"))
    for case in cases:
        for action in case.get("expected_actions", []):
            assert is_known(action), f"{action} missing from the registry"


def test_resolve_passes_through_canonical_ids():
    assert resolve("order_ecg_within_10min") == "order_ecg_within_10min"


def test_resolve_rewrites_deprecated_ids():
    assert resolve("give_aspirin") == "give_aspirin_325_chewed"
    assert resolve("order_ecg_stat") == "order_ecg_within_10min"


def test_resolve_returns_none_for_unknown():
    assert resolve("teleport_patient") is None


def test_every_deprecated_target_is_itself_known():
    for old, new in DEPRECATED_ACTION_IDS.items():
        if new is not None:
            assert new in KNOWN_ACTION_IDS, f"{old} -> {new} which is not canonical"


def test_category_lookup_follows_deprecation():
    assert category_of("give_aspirin") is ActionCategory.THERAPEUTIC
    assert category_of("order_ecg_stat") is ActionCategory.DIAGNOSTIC
    assert category_of("nonsense") is None


# ── tiering ──────────────────────────────────────────────────────────────────

def test_tier_gold_needs_reviewer_grading_and_guidelines():
    assert classify_tier({
        "clinical_reviewer": "dr_b",
        "grading_blueprint": {"hits": []},
        "guideline_versions": ["AHA-2025"],
    }) is CorpusTier.GOLD


def test_tier_silver_has_grading_plus_one_provenance_leg():
    assert classify_tier({
        "grading_blueprint": {"hits": []}, "clinical_reviewer": "dr_b",
    }) is CorpusTier.SILVER
    assert classify_tier({
        "grading_blueprint": {"hits": []}, "guideline_versions": ["x"],
    }) is CorpusTier.SILVER


def test_tier_bronze_has_grading_only():
    assert classify_tier({"grading_blueprint": {"hits": []}}) is CorpusTier.BRONZE


def test_tier_draft_has_no_grading():
    assert classify_tier({}) is CorpusTier.DRAFT
    assert classify_tier({"clinical_reviewer": "dr_b"}) is CorpusTier.DRAFT


def test_reviewer_alone_cannot_reach_gold():
    """Provenance without a rubric still is not gradable, so not GOLD."""
    tier = classify_tier({"clinical_reviewer": "dr_b", "guideline_versions": ["x"]})
    assert tier is CorpusTier.DRAFT


# ── conversion ───────────────────────────────────────────────────────────────

def test_well_formed_v1_converts():
    result = migrate_case_v1_to_v2(_v1())
    assert result.converted is True
    assert result.blueprint is not None
    assert result.blueprint.case_id == "PRB-001"
    assert result.missing_required == []


def test_converted_case_carries_its_content_hash():
    result = migrate_case_v1_to_v2(_v1())
    assert result.blueprint.provenance.content_hash
    assert result.blueprint.content_hash_matches is True


def test_clinical_truth_is_mapped_from_hidden_truth():
    ct = migrate_case_v1_to_v2(_v1()).blueprint.clinical_truth
    assert ct.primary_diagnosis == "Inferior wall STEMI"
    assert ct.icd10 == "I21.19"
    assert ct.symptoms_present == ("chest pain",)
    assert ct.absent_findings == ("fever",)
    assert ct.onset_minutes_ago == 40


def test_red_herrings_become_environment_distractors():
    env = migrate_case_v1_to_v2(_v1()).blueprint.environment
    assert env.distractors == ("blames gas",)


def test_family_present_becomes_a_persona():
    assert migrate_case_v1_to_v2(_v1()).blueprint.interaction.family_personas == ("attendant",)
    no_family = migrate_case_v1_to_v2(_v1(family_present=False))
    assert no_family.blueprint.interaction.family_personas == ()


# ── missing required fields block conversion ──────────────────────────────────

def test_missing_case_id_blocks_conversion():
    v1 = _v1()
    del v1["case_id"]
    result = migrate_case_v1_to_v2(v1)
    assert result.converted is False
    assert result.blueprint is None
    assert "identity.case_id" in result.missing_required


def test_missing_demographics_blocks_conversion():
    v1 = _v1()
    del v1["demographics"]
    result = migrate_case_v1_to_v2(v1)
    assert result.converted is False
    assert "patient.demographics" in result.missing_required


def test_missing_diagnosis_blocks_conversion():
    v1 = _v1(hidden={"primary_diagnosis": ""})
    result = migrate_case_v1_to_v2(v1)
    assert result.converted is False
    assert "clinical_truth.primary_diagnosis" in result.missing_required


# ── inferred vs defaulted ────────────────────────────────────────────────────

def test_present_fields_are_inferred_not_defaulted():
    report = migrate_case_v1_to_v2(_v1()).report
    assert "targeting.difficulty" in report.inferred
    assert "targeting.difficulty" not in report.defaulted
    assert "patient.demographics" in report.inferred


def test_absent_fields_are_defaulted_for_review():
    report = migrate_case_v1_to_v2(_v1()).report
    assert "targeting.roles" in report.defaulted
    assert "provenance.clinical_reviewer" in report.defaulted
    assert report.needs_review is True


def test_the_two_buckets_never_overlap():
    report = migrate_case_v1_to_v2(_v1()).report
    assert not set(report.inferred) & set(report.defaulted)


def test_declared_assessment_mode_is_inferred():
    result = migrate_case_v1_to_v2(_v1(assessment_mode="summative"))
    assert result.blueprint.assessment_mode is AssessmentMode.SUMMATIVE
    assert "identity.assessment_mode" in result.report.inferred


def test_absent_assessment_mode_defaults_to_practice():
    """Defaulting must not promote an unreviewed case into credential use."""
    result = migrate_case_v1_to_v2(_v1())
    assert result.blueprint.assessment_mode is AssessmentMode.PRACTICE
    assert "identity.assessment_mode" in result.report.defaulted


def test_empty_allergy_list_is_flagged_rather_than_trusted():
    """'Confirmed none' and 'never asked' are clinically different."""
    report = migrate_case_v1_to_v2(_v1()).report
    assert "clinical_truth.allergies" in report.defaulted
    assert any("allergies" in n and "unknown" in n for n in report.notes)


def test_populated_allergy_list_is_inferred():
    v1 = _v1()
    v1["hidden"]["allergies"] = ["penicillin"]
    report = migrate_case_v1_to_v2(v1).report
    assert "clinical_truth.allergies" in report.inferred


# ── obsolete action identifiers ──────────────────────────────────────────────

def test_canonical_actions_produce_no_warning():
    assert migrate_case_v1_to_v2(_v1()).warnings == []


def test_deprecated_actions_are_reported_as_rewritable():
    result = migrate_case_v1_to_v2(_v1(expected_actions=["give_aspirin", "order_ecg_stat"]))
    joined = " ".join(result.warnings)
    assert "obsolete_action_ids" in joined
    assert "rewritable_action_ids" in joined
    assert "give_aspirin_325_chewed" in joined


def test_unknown_actions_are_reported_as_retired():
    result = migrate_case_v1_to_v2(_v1(expected_actions=["teleport_patient"]))
    joined = " ".join(result.warnings)
    assert "retired_action_ids" in joined


# ── corpus driver ────────────────────────────────────────────────────────────

def test_load_corpus_handles_a_json_list_file():
    assert len(load_corpus(SEED_CORPUS)) >= 1


def test_load_corpus_handles_a_directory(tmp_path: Path):
    (tmp_path / "a.json").write_text(json.dumps(_v1(case_id="A")), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps([_v1(case_id="B")]), encoding="utf-8")
    ids = {c["case_id"] for c in load_corpus(tmp_path)}
    assert ids == {"A", "B"}


def test_run_migration_against_the_real_seed_corpus():
    report = run_migration(SEED_CORPUS)
    assert report.examined >= 1
    assert report.converted == report.examined
    # The seed corpus has no reviewer, guidelines, or rubric, so it is DRAFT.
    assert report.by_tier["draft"] == report.examined
    assert report.missing_grading_blueprint == report.examined
    assert report.missing_provenance == report.examined


def test_corpus_report_splits_inferred_from_defaulted_in_render():
    report = run_migration(SEED_CORPUS)
    rendered = report.render()
    assert "Inferred from source:" in rendered
    assert "Defaulted (needs review):" in rendered
    assert "cases examined" in rendered


def test_corpus_report_lists_a_review_queue():
    report = run_migration(SEED_CORPUS)
    assert report.review_queue
    assert "Clinical review queue:" in report.render()


def test_corpus_report_is_json_serializable():
    blob = json.dumps(run_migration(SEED_CORPUS).to_dict())
    assert "by_tier" in blob


def test_corpus_report_counts_are_consistent():
    report = run_migration(SEED_CORPUS)
    assert sum(report.by_tier.values()) == report.examined
    assert len(report.results) == report.examined


def test_empty_corpus_reports_nothing(tmp_path: Path):
    report = run_migration(tmp_path)
    assert report.examined == 0
    assert report.review_queue == []
