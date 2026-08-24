"""
Matcher kind registry — single source of truth for Nirikshak and the authoring harness.

Import this module; never duplicate the kind list in harness-local code.
"""
from __future__ import annotations

KNOWN_MATCHER_KINDS = frozenset({
    "drug_given",
    "drug_not_given",
    "order_placed",
    "diagnosis_stated",
    "vital_recognized",
    "finding_recognized",
    "symptom_elicited",
    "physical_sign_identified",
    "escalation",
    "handoff_given",
    "sequence",
    "action_within",
    "flag_present",
    "flag_cleared",
})
