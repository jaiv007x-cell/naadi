"""Load case and grading blueprints from persisted case JSON."""
from __future__ import annotations

from datetime import datetime

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
from shared.schemas.grading import Axis, GradingBlueprint, RubricHit, Severity


def _grading_blueprint_from_section(raw: dict) -> GradingBlueprint:
    hits = tuple(
        RubricHit(
            id=hit["id"],
            axis=Axis(hit["axis"]),
            matcher=hit["matcher"],
            params=hit.get("params") or {},
            points=float(hit.get("points", 1.0)),
            severity=Severity(hit.get("severity", Severity.MINOR.value)),
            required=bool(hit.get("required", False)),
            fail_case_on_violation=bool(hit.get("fail_case_on_violation", False)),
            competencies=tuple(hit.get("competencies") or ()),
            description=hit.get("description") or "",
        )
        for hit in raw.get("hits") or ()
    )
    weights_raw = raw.get("weights") or {}
    weights = {Axis(k): float(v) for k, v in weights_raw.items()} if weights_raw else None
    bands_raw = raw.get("letter_bands") or ()
    letter_bands = tuple((float(lo), letter) for lo, letter in bands_raw) if bands_raw else None
    kwargs: dict = {
        "case_id": raw["case_id"],
        "case_version": raw["case_version"],
        "rubric_version": raw["rubric_version"],
        "hits": hits,
        "display_name": raw.get("display_name") or "",
        "pass_threshold": float(raw.get("pass_threshold", 0.70)),
    }
    if weights is not None:
        kwargs["weights"] = weights
    if letter_bands is not None:
        kwargs["letter_bands"] = letter_bands
    return GradingBlueprint(**kwargs)


def grading_blueprint_from_case_json(blueprint_json: dict) -> GradingBlueprint:
    raw = blueprint_json.get("grading_blueprint")
    if raw is None:
        raise ValueError("case draft has no grading_blueprint")
    return _grading_blueprint_from_section(raw)


def blueprint_from_case_json(blueprint_json: dict) -> CaseBlueprintV2:
    """Rehydrate a CaseBlueprintV2 from persisted JSON (inverse of to_dict)."""
    identity_raw = blueprint_json["identity"]
    targeting_raw = blueprint_json["targeting"]
    patient_raw = blueprint_json["patient"]
    clinical_raw = blueprint_json["clinical_truth"]
    environment_raw = blueprint_json["environment"]
    physiology_raw = blueprint_json["physiology"]
    interaction_raw = blueprint_json["interaction"]
    provenance_raw = blueprint_json.get("provenance") or {}

    demographics = Demographics(**patient_raw["demographics"])
    language = Language(patient_raw.get("language", demographics.native_language))
    initial_vitals = Vital(**physiology_raw["initial_vitals"])

    grading_raw = blueprint_json.get("grading_blueprint")
    grading = _grading_blueprint_from_section(grading_raw) if grading_raw else None

    reviewed_at_raw = provenance_raw.get("reviewed_at")
    reviewed_at = datetime.fromisoformat(reviewed_at_raw) if reviewed_at_raw else None

    return CaseBlueprintV2(
        identity=CaseIdentity(
            case_id=identity_raw["case_id"],
            version=str(identity_raw["version"]),
            title=str(identity_raw["title"]),
            corpus_tier=CorpusTier(identity_raw["corpus_tier"]),
            assessment_mode=AssessmentMode(identity_raw["assessment_mode"]),
        ),
        targeting=CaseTargeting(
            roles=tuple(targeting_raw.get("roles") or ()),
            competencies=tuple(targeting_raw.get("competencies") or ()),
            difficulty=float(targeting_raw["difficulty"]),
            prerequisites=tuple(targeting_raw.get("prerequisites") or ()),
        ),
        patient=PatientPersona(
            demographics=demographics,
            language=language,
            voice_profile=str(patient_raw.get("voice_profile") or "default"),
            literacy_years=int(patient_raw.get("literacy_years") or 8),
            persona_notes=str(patient_raw.get("persona_notes") or ""),
        ),
        clinical_truth=ClinicalTruth(
            primary_diagnosis=str(clinical_raw.get("primary_diagnosis") or ""),
            icd10=str(clinical_raw.get("icd10") or ""),
            differentials=tuple(clinical_raw.get("differentials") or ()),
            symptoms_present=tuple(clinical_raw.get("symptoms_present") or ()),
            findings_present=tuple(clinical_raw.get("findings_present") or ()),
            absent_findings=tuple(clinical_raw.get("absent_findings") or ()),
            allergies=tuple(clinical_raw.get("allergies") or ()),
            medications=tuple(clinical_raw.get("medications") or ()),
            onset_minutes_ago=int(clinical_raw.get("onset_minutes_ago") or 0),
        ),
        environment=EnvironmentSpec(
            setting=str(environment_raw.get("setting") or "opd"),
            resources=tuple(environment_raw.get("resources") or ()),
            distractors=tuple(environment_raw.get("distractors") or ()),
            difficulty_parameters=dict(environment_raw.get("difficulty_parameters") or {}),
        ),
        physiology=PhysiologySpec(
            engine_version=str(physiology_raw.get("engine_version") or "0.2.0"),
            initial_vitals=initial_vitals,
            deterioration_rules=tuple(physiology_raw.get("deterioration_rules") or ()),
            pharmacology_profile=str(physiology_raw.get("pharmacology_profile") or "default"),
        ),
        interaction=InteractionSpec(
            allowed_disclosures=tuple(interaction_raw.get("allowed_disclosures") or ()),
            hidden_facts=tuple(interaction_raw.get("hidden_facts") or ()),
            family_personas=tuple(interaction_raw.get("family_personas") or ()),
            dialogue_constraints=tuple(interaction_raw.get("dialogue_constraints") or ()),
        ),
        grading_blueprint=grading,
        provenance=Provenance(
            author=str(provenance_raw.get("author") or "unknown"),
            clinical_reviewer=provenance_raw.get("clinical_reviewer"),
            sources=tuple(provenance_raw.get("sources") or ()),
            guideline_versions=tuple(provenance_raw.get("guideline_versions") or ()),
            reviewed_at=reviewed_at,
            content_hash=str(provenance_raw.get("content_hash") or ""),
        ),
        schema_version=str(blueprint_json.get("schema_version") or CASE_SCHEMA_VERSION),
        probe_only=bool(blueprint_json.get("probe_only", False)),
    )


def content_hash_from_case_json(blueprint_json: dict) -> str:
    return blueprint_from_case_json(blueprint_json).compute_content_hash()


def validation_errors_from_case_json(blueprint_json: dict) -> list[str]:
    return blueprint_from_case_json(blueprint_json).validation_errors()
