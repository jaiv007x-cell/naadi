"""
Canonical registry of clinical action identifiers.

Before this existed, action ids were spelled out independently in
`PhysiologyEngine.apply_action`, the `CaseBlueprint.expected_orders` /
`expected_procedure_sequence` maps, the seed corpus, and the ledger's timing
windows. Nothing validated them, and `apply_action` returns
`{"unknown": True}` for anything it does not recognize — so a typo in a case
file degraded silently into an action the learner could never satisfy.

The migration script uses this to flag obsolete identifiers, which is the
scenario compiler's regression list.

Note that Nirikshak matcher ids (`oxygen`, `iv_fluids`, `ecg_12l`) are a
separate namespace: those name trace events, not learner actions. Mapping the
two is the scenario compiler's job, not this module's.
"""
from __future__ import annotations

from enum import Enum


class ActionCategory(str, Enum):
    COMMUNICATION = "communication"
    ASSESSMENT = "assessment"
    DIAGNOSTIC = "diagnostic"
    THERAPEUTIC = "therapeutic"
    DISPOSITION = "disposition"


# action id -> category. Sourced from apply_action, the CaseBlueprint action
# maps, seed_corpus.json, and the ledger timing windows.
ACTION_CATALOG: dict[str, ActionCategory] = {
    # communication
    "greet_patient_in_native_language": ActionCategory.COMMUNICATION,
    "take_focused_history": ActionCategory.ASSESSMENT,
    "obtain_consent_transfer": ActionCategory.COMMUNICATION,
    # assessment
    "measure_vitals": ActionCategory.ASSESSMENT,
    # diagnostic
    "order_ecg": ActionCategory.DIAGNOSTIC,
    "order_ecg_within_10min": ActionCategory.DIAGNOSTIC,
    "order_troponin": ActionCategory.DIAGNOSTIC,
    "order_ct_head": ActionCategory.DIAGNOSTIC,
    # therapeutic
    "give_aspirin_325_chewed": ActionCategory.THERAPEUTIC,
    "give_atorvastatin_80": ActionCategory.THERAPEUTIC,
    "give_morphine": ActionCategory.THERAPEUTIC,
    "give_oxygen": ActionCategory.THERAPEUTIC,
    "give_streptokinase": ActionCategory.THERAPEUTIC,
    "give_iv_fluid_ns": ActionCategory.THERAPEUTIC,
    # disposition
    "arrange_pci_transfer": ActionCategory.DISPOSITION,
}

KNOWN_ACTION_IDS: frozenset[str] = frozenset(ACTION_CATALOG)

# Identifiers that appeared in earlier corpus revisions, mapped to their
# replacement so the migration can rewrite rather than merely warn.
# `None` means retired with no direct equivalent.
DEPRECATED_ACTION_IDS: dict[str, str | None] = {
    "order_ecg_stat": "order_ecg_within_10min",
    "give_aspirin": "give_aspirin_325_chewed",
    "give_statin": "give_atorvastatin_80",
    "transfer_pci": "arrange_pci_transfer",
    "greet_patient": "greet_patient_in_native_language",
    "check_vitals": "measure_vitals",
    "take_history": "take_focused_history",
}

# ISO dates for compiler messages. Replacement lives in DEPRECATED_ACTION_IDS.
DEPRECATED_ACTION_RETIRED_ON: dict[str, str] = {
    "order_ecg_stat": "2025-11-03",
    "give_aspirin": "2025-11-03",
    "give_statin": "2025-11-03",
    "transfer_pci": "2025-11-03",
    "greet_patient": "2025-11-03",
    "check_vitals": "2025-11-03",
    "take_history": "2025-11-03",
}


def is_known(action_id: str) -> bool:
    return action_id in KNOWN_ACTION_IDS


def resolve(action_id: str) -> str | None:
    """
    Map an action id to its canonical form.

    Returns the id unchanged when already canonical, the replacement when
    deprecated with a known successor, and `None` when unrecognized or retired
    outright.
    """
    if action_id in KNOWN_ACTION_IDS:
        return action_id
    return DEPRECATED_ACTION_IDS.get(action_id)


def category_of(action_id: str) -> ActionCategory | None:
    canonical = resolve(action_id)
    return ACTION_CATALOG.get(canonical) if canonical else None
