"""Immutable SAMVAAD reference rubrics v1.

REFERENCE_RUBRICS_VERSION + CONTENT_HASH pin the bytes. Any change requires
samvaad.reference_rubrics.v2 — never mutate v1 in place (pin 3).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Final

from services.pratibimb.samvaad.constants import DOMAINS, REFERENCE_RUBRICS_VERSION
from services.pratibimb.samvaad.fail_case_allowlist import FAIL_CASE_ALLOWLIST
from services.pratibimb.samvaad.invariants import FORBIDDEN_PRESENTATION_FEATURES
from services.pratibimb.samvaad.matcher_spec import (
    MATCHER_KIND,
    REQUIRED_STEPS,
    SUPPORTING_CUE_PARAM,
    empathy_matcher_params,
)

# One worked rubric per domain A–I — Framework v1 reference shapes.
_REFERENCE: Final[tuple[dict[str, Any], ...]] = (
    {
        "domain": "A",
        "competency": "comm.patient.register_match",
        "matcher": {"kind": "register_appropriateness"},
        "params": {
            "patient_literacy_band": "low",
            "patient_languages": ["hi"],
            "disallow_unexplained_jargon": True,
            "reward_vernacular_substitution": True,
            "forbid_language_detection_as_sole_pass": True,
        },
        "required": True,
        "points": 12,
        "fail_case_on_violation": False,
    },
    {
        "domain": "B",
        "competency": "comm.empathy.acknowledge_distress",
        "matcher": {"kind": MATCHER_KIND, "required": list(REQUIRED_STEPS)},
        "params": empathy_matcher_params(
            supporting_cue_phrases=["aap ghabraiye mat", "main yahan hoon"],
        ),
        "required": False,
        "points": 8,
        "fail_case_on_violation": False,
        "assessment_mode": "formative",
    },
    {
        "domain": "C",
        "competency": "comm.consent.vernacular_risk_teachback",
        "matcher": {
            "kind": "action_sequence",
            "required": [
                "state_procedure_purpose_vernacular",
                "state_material_risks_vernacular",
                "elicit_voluntary_consent_signal",
                "teach_back_risk_or_purpose",
            ],
        },
        "params": {},
        "required": True,
        "points": 12,
        "fail_case_on_violation": False,
    },
    {
        "domain": "D",
        "competency": "comm.handoff.sbar_complete",
        "matcher": {
            "kind": "sbar_fields_present",
            "required_elements": [
                "situation",
                "background",
                "assessment",
                "recommendation",
            ],
        },
        "params": {},
        "required": True,
        "points": 15,
        "fail_case_on_violation": True,
    },
    {
        "domain": "E",
        "competency": "team.hierarchy.safety_challenge",
        "matcher": {
            "kind": "action_sequence",
            "required": [
                "state_safety_concern_clearly",
                "closed_loop_or_readback",
                "escalate_or_repeat_if_ignored",
            ],
        },
        "params": {"scenario_class": "junior_notices_unsafe_senior_action"},
        "required": True,
        "points": 14,
        "fail_case_on_violation": True,
    },
    {
        "domain": "F",
        "competency": "comm.family.deescalate_without_capitulation",
        "matcher": {
            "kind": "action_sequence",
            "required": [
                "acknowledge_family_concern",
                "name_clinical_boundary_or_next_step",
                "avoid_promise_beyond_scope",
            ],
        },
        "params": {},
        "required": False,
        "points": 8,
        "fail_case_on_violation": False,
    },
    {
        "domain": "G",
        "competency": "pres.uniform.badge_visible",
        "matcher": {
            "kind": "visual_frame_check",
            "checks": [
                "badge_visible",
                "uniform_or_ppe_complete",
                "closed_shoes_if_clinical",
            ],
        },
        "params": {},
        "forbidden_features": sorted(FORBIDDEN_PRESENTATION_FEATURES),
        "required": True,
        "points": 4,
        "fail_case_on_violation": False,
    },
    {
        "domain": "H",
        "competency": "digi.patient_id.two_source_verify",
        "matcher": {
            "kind": "action_sequence",
            "required": ["scan_wristband", "confirm_patient_on_screen"],
        },
        "params": {},
        "required": True,
        "points": 15,
        "fail_case_on_violation": True,
    },
    {
        "domain": "H",
        "competency": "digi.terminal.logout_on_shift_end",
        "matcher": {"kind": "action_present", "action": "shared_terminal_logout"},
        "params": {},
        "required": True,
        "points": 12,
        "fail_case_on_violation": True,
    },
    {
        "domain": "I",
        "competency": "stress.self_regulation.technique_deployed",
        "matcher": {"kind": "debrief_self_report_structured"},
        "params": {
            "techniques_referenced": [
                "box_breathing",
                "grounding_5_4_3_2_1",
                "brief_pause_reset",
            ],
            "min_occurrences": 1,
            "reject_vague": ["I stayed calm", "I was fine"],
        },
        "required": False,
        "points": 6,
        "fail_case_on_violation": False,
    },
)


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def reference_rubrics_v1() -> tuple[dict[str, Any], ...]:
    """Return copies so callers cannot mutate the frozen module tuple."""
    return tuple(json.loads(json.dumps(r)) for r in _REFERENCE)


def reference_content_hash() -> str:
    payload = {
        "version": REFERENCE_RUBRICS_VERSION,
        "rubrics": list(_REFERENCE),
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


CONTENT_HASH: Final[str] = reference_content_hash()


def domains_present() -> frozenset[str]:
    return frozenset(r["domain"] for r in _REFERENCE)


def fail_case_competencies() -> frozenset[str]:
    return frozenset(
        r["competency"] for r in _REFERENCE if r.get("fail_case_on_violation") is True
    )


def assert_fail_cases_allowlisted() -> None:
    extra = fail_case_competencies() - FAIL_CASE_ALLOWLIST
    if extra:
        raise ValueError(f"fail_case competencies not on allowlist: {sorted(extra)}")


def assert_domains_complete() -> None:
    missing = DOMAINS - domains_present()
    if missing:
        raise ValueError(f"reference rubrics missing domains: {sorted(missing)}")


assert SUPPORTING_CUE_PARAM in _REFERENCE[1]["params"]
