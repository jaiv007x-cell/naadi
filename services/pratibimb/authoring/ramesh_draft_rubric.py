"""Nine-hit draft rubric for Ramesh Kale inferior STEMI (post-.e dialogue/rubric pass)."""
from __future__ import annotations

from copy import deepcopy

from services.pratibimb.app.eval.rubric import Axis, Severity

RAMESH_DRAFT_RUBRIC_VERSION = "0.2.0-draft-9hit"

RAMESH_DRAFT_HITS: tuple[dict, ...] = (
    {
        "id": "stemi.inferior_ecg_recognized",
        "axis": Axis.DIAGNOSTIC.value,
        "matcher": "finding_recognized",
        "params": {"finding_id": "inferior_st_elevation", "within_s": 600},
        "points": 12.0,
        "severity": Severity.CRITICAL.value,
        "required": True,
        "fail_case_on_violation": True,
        "description": "Recognizes inferior STEMI on initial 12-lead ECG",
    },
    {
        "id": "stemi.v4r_before_nitrates",
        "axis": Axis.SAFETY.value,
        "matcher": "order_placed",
        "params": {"order_id": "ecg_right_sided", "within_s": 900},
        "points": 15.0,
        "severity": Severity.CRITICAL.value,
        "required": True,
        "fail_case_on_violation": True,
        "description": "Requests right-sided leads / identifies RV involvement before nitrates",
    },
    {
        "id": "stemi.rv_hypotension_fluid_rescue",
        "axis": Axis.ACTION.value,
        "matcher": "drug_given",
        "params": {"drug_id": "iv_fluid_ns", "route": "IV", "within_s": 900},
        "points": 12.0,
        "severity": Severity.CRITICAL.value,
        "required": True,
        "fail_case_on_violation": True,
        "description": "Fluid bolus rescue on RV preload-dependent hypotension",
    },
    {
        "id": "stemi.aspirin_timing",
        "axis": Axis.ACTION.value,
        "matcher": "drug_given",
        "params": {
            "drug_id": "aspirin",
            "route": "PO",
            "min_dose": 300,
            "max_dose": 350,
            "within_s": 600,
        },
        "points": 8.0,
        "severity": Severity.MAJOR.value,
        "required": False,
        "fail_case_on_violation": False,
        "description": "Aspirin 325 mg chewed within 10 minutes",
    },
    {
        "id": "stemi.pain_reassessment",
        "axis": Axis.ACTION.value,
        "matcher": "vital_recognized",
        "params": {"vital_id": "pain_scale", "within_s": 900},
        "points": 5.0,
        "severity": Severity.MINOR.value,
        "required": False,
        "fail_case_on_violation": False,
        "description": "Reassesses pain scale after analgesia or intervention",
    },
    {
        "id": "stemi.communication_register",
        "axis": Axis.COMMUNICATION.value,
        "matcher": "action_within",
        "params": {"action_id": "greet_patient_in_native_language", "within_s": 120},
        "points": 6.0,
        "severity": Severity.MINOR.value,
        "required": False,
        "fail_case_on_violation": False,
        "description": "Marathi-first Roman register with code-switched clinical English",
    },
    {
        "id": "stemi.atropine_symptomatic_brady",
        "axis": Axis.ACTION.value,
        "matcher": "drug_given",
        "params": {"drug_id": "atropine", "within_s": 900},
        "points": 8.0,
        "severity": Severity.MAJOR.value,
        "required": False,
        "fail_case_on_violation": False,
        "description": "Atropine for symptomatic bradycardia / AV block (inferior RCA)",
    },
    {
        "id": "stemi.cath_lab_activation",
        "axis": Axis.ACTION.value,
        "matcher": "escalation",
        "params": {"target": "pci_transfer", "within_s": 900},
        "points": 6.0,
        "severity": Severity.MINOR.value,
        "required": False,
        "fail_case_on_violation": False,
        "description": "PCI-capable transfer arranged",
    },
    {
        "id": "stemi.allergy_check",
        "axis": Axis.SAFETY.value,
        "matcher": "symptom_elicited",
        "params": {"symptom_id": "drug_allergy", "within_s": 300},
        "points": 4.0,
        "severity": Severity.MINOR.value,
        "required": False,
        "fail_case_on_violation": False,
        "description": "Allergy history elicited before aspirin",
    },
)

RAMESH_DIALOGUE_CONSTRAINTS: tuple[str, ...] = (
    "turn_budget:20-30",
    "register:mr_roman_transliteration:code_switch_clinical_english",
    "opening:attendant_present:gas_attribution_distractor",
    "branch:order_ecg_12l:unlocks:inferior_st_elevation_disclosure",
    "branch:order_ecg_right_sided:discoverable:rv_infarct_possible",
    "branch:nitrate_admin:gated:rv_flag_cleared_or_v4r_negative",
    "branch:rv_hypotension_after_nitrate:fluid_bolus_rescue_path",
    "branch:symptomatic_brady:atropine_responsive_inferior_av_block",
    "cue:empathy_window:min_occurrences_per_turn=2",
    "cue:allow_implicit_cue:gas_distractor_redirect",
)


def ramesh_draft_grading_blueprint(*, case_id: str) -> dict:
    return {
        "case_id": case_id,
        "case_version": "1.0.0-draft-dialogue",
        "rubric_version": RAMESH_DRAFT_RUBRIC_VERSION,
        "display_name": "Ramesh Kale — inferior STEMI (9-hit draft rubric)",
        "pass_threshold": 0.70,
        "hits": [deepcopy(h) for h in RAMESH_DRAFT_HITS],
    }
