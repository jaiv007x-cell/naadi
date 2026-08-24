"""Deserialize runtime CaseBlueprint from a corpus envelope_json TEXT blob."""
from __future__ import annotations

import json
from typing import Any

from shared.schemas.case import (
    CaseBlueprint,
    Demographics,
    HiddenTruth,
    Language,
    PatientPhysiology,
    Vital,
)


def case_blueprint_from_envelope_json(envelope_json: str) -> CaseBlueprint:
    """
    Build the V1 physio CaseBlueprint from frozen corpus envelope bytes.

    Envelope ``blueprint`` may be V2 authoring JSON or a V1-shaped dict.
    Adaptation lives at this boundary only (Q3) — corpus stores one format.
    """
    envelope = json.loads(envelope_json)
    raw = envelope.get("blueprint") or {}
    if "identity" in raw:
        return _v2_dict_to_case_blueprint(raw)
    return CaseBlueprint(**raw)


def _v2_dict_to_case_blueprint(raw: dict[str, Any]) -> CaseBlueprint:
    identity = raw.get("identity") or {}
    targeting = raw.get("targeting") or {}
    patient = raw.get("patient") or {}
    truth = raw.get("clinical_truth") or {}
    environment = raw.get("environment") or {}
    physiology = raw.get("physiology") or {}
    interaction = raw.get("interaction") or {}

    demo_raw = patient.get("demographics") or {}
    lang_code = demo_raw.get("native_language") or patient.get("language") or "en"
    demographics = Demographics(
        name=str(demo_raw.get("name") or "Patient"),
        age=int(demo_raw.get("age") or 40),
        sex=demo_raw.get("sex") or "O",
        occupation=str(demo_raw.get("occupation") or ""),
        state=str(demo_raw.get("state") or ""),
        city=str(demo_raw.get("city") or ""),
        native_language=Language(lang_code),
        education_years=int(demo_raw.get("education_years") or 0),
        monthly_income_inr=int(demo_raw.get("monthly_income_inr") or 0),
    )
    vitals_raw = physiology.get("initial_vitals") or {}
    baseline = Vital(
        hr=float(vitals_raw.get("hr") or 80),
        sbp=float(vitals_raw.get("sbp") or 120),
        dbp=float(vitals_raw.get("dbp") or 80),
        spo2=float(vitals_raw.get("spo2") or 98),
        rr=float(vitals_raw.get("rr") or 16),
        temp_c=float(vitals_raw.get("temp_c") or 36.8),
    )
    hidden = HiddenTruth(
        primary_diagnosis=str(truth.get("primary_diagnosis") or "unknown"),
        icd10=str(truth.get("icd10") or ""),
        onset_minutes_ago=int(truth.get("onset_minutes_ago") or 0),
        symptoms_present=list(truth.get("symptoms_present") or []),
        symptoms_absent=list(truth.get("absent_findings") or []),
        comorbidities=list(truth.get("comorbidities") or []),
        current_meds=list(truth.get("medications") or []),
        allergies=list(truth.get("allergies") or []),
        social_history=dict(truth.get("social_history") or {}),
        red_herrings=list(truth.get("red_herrings") or []),
    )
    difficulty = float(targeting.get("difficulty") or 0.5)
    time_pressure = int(
        (environment.get("difficulty_parameters") or {}).get("time_pressure_seconds")
        or 900
    )
    # Chief complaint: V2 may only expose interaction disclosures — use a stable placeholder.
    disclosures = list(interaction.get("allowed_disclosures") or ["symptom"])
    complaint = disclosures[0] if disclosures else "symptom"
    lang = demographics.native_language
    return CaseBlueprint(
        case_id=str(identity.get("case_id") or "unknown"),
        difficulty=difficulty,
        demographics=demographics,
        chief_complaint_verbatim={lang: complaint, Language.ENGLISH: complaint},
        baseline_vitals=baseline,
        hidden=hidden,
        expected_actions=list(truth.get("expected_actions") or []),
        critical_actions=list(truth.get("critical_actions") or []),
        time_pressure_seconds=time_pressure,
        family_present=bool((interaction.get("family_personas") or [])),
        resources_available=list(environment.get("resources") or []),
        patient_physiology=PatientPhysiology(
            weight_kg=float(physiology.get("weight_kg") or 70.0),
            height_cm=float(physiology.get("height_cm") or 170.0),
        ),
    )
