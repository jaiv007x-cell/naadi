"""
Controlled vocabulary for flag clear reasons and human-readable cause registry.

FlagCause lives in `shared.schemas.flag_cause` (dotted namespace enum).
"""
from __future__ import annotations

from enum import Enum

from shared.schemas.flag_cause import FlagCause

__all__ = [
    "FlagCause",
    "FlagClearReason",
    "CAUSE_REGISTRY",
    "CLEAR_REGISTRY",
]


class FlagClearReason(str, Enum):
    REVERSAL_AGENT = "reversal_agent"
    CORRECTIVE_ACTION = "corrective_action"
    TTL_EXPIRED = "ttl_expired"
    DRUG_WEAROFF = "drug_wearoff"
    SPONTANEOUS_RESOLUTION = "spontaneous_resolution"
    RESOLVED = "resolved"
    CASE_END = "case_end"
    INSTRUCTOR_CLEARED = "instructor_cleared"


CAUSE_REGISTRY: dict[FlagCause, str] = {
    FlagCause.DRUG_ADMIN: "Drug administered",
    FlagCause.DRUG_ALLERGY: "Allergic reaction to administered drug",
    FlagCause.DRUG_CONTRAINDICATED: "Drug given despite contraindication predicate",
    FlagCause.DRUG_PD_EFFECT: "Pharmacodynamic effect at reference dose",
    FlagCause.DRUG_PK_CLEARANCE: "PK clearance modifier produced flag",
    FlagCause.DRUG_TOXIC: "Plasma concentration exceeded toxic threshold",
    FlagCause.DRUG_REVERSAL: "Reversal agent cleared proximate drug effect",
    FlagCause.DRUG_INTERACTION: "Drug-drug interaction produced combined flag",
    FlagCause.PHYSIOLOGY_HOMEOSTASIS: "Physiology model returned toward baseline",
    FlagCause.PHYSIOLOGY_COMPENSATION: "Compensatory physiology resolved flag",
    FlagCause.PHYSIOLOGY_DECOMPENSATION: "Decompensation crossed clinical threshold",
    FlagCause.PHYSIOLOGY_TIMEOUT: "Flag TTL elapsed",
    FlagCause.PHYSIOLOGY_PHARMACODYNAMIC: "PK/PD overlay raised flag without pending cause",
    FlagCause.CLINICAL_SEPSIS_SUSPECTED: "Sepsis threshold crossed",
    FlagCause.CLINICAL_HYPOXIA: "Hypoxia threshold crossed",
    FlagCause.CLINICAL_HYPOTENSION: "Hypotension threshold crossed",
    FlagCause.CLINICAL_HYPERTENSION: "Hypertension threshold crossed",
    FlagCause.CLINICAL_ARRHYTHMIA: "Arrhythmia threshold crossed",
    FlagCause.CLINICAL_ANAPHYLAXIS_ONSET: "Anaphylaxis cascade initiated",
    FlagCause.CLINICAL_AIRWAY_COMPROMISE: "Airway compromise detected",
    FlagCause.CLINICAL_ALTERED_MENTATION: "Altered mentation detected",
    FlagCause.PROCEDURE_STARTED: "Procedure started",
    FlagCause.PROCEDURE_COMPLETED: "Procedure completed",
    FlagCause.PROCEDURE_FAILED: "Procedure failed",
    FlagCause.PROCEDURE_COMPLICATION: "Procedure complication",
    FlagCause.CASE_START: "Case session started",
    FlagCause.CASE_END: "Case session ended",
    FlagCause.CASE_PERTURBATION: "Scenario perturbation injected",
    FlagCause.CASE_INSTRUCTOR_INJECT: "Instructor injected scenario event",
    FlagCause.LEARNER_RECOGNITION: "Learner recognition event",
    FlagCause.LEARNER_ORDER: "Learner order placed",
    FlagCause.LEARNER_HANDOFF: "Learner handoff recorded",
    FlagCause.LEARNER_ESCALATION: "Learner escalation recorded",
}

CLEAR_REGISTRY: dict[FlagClearReason, str] = {
    FlagClearReason.REVERSAL_AGENT: "Pharmacological reversal agent administered",
    FlagClearReason.CORRECTIVE_ACTION: "Clinician action addressed flag's proximate cause",
    FlagClearReason.TTL_EXPIRED: "Flag's time-to-live elapsed",
    FlagClearReason.DRUG_WEAROFF: "Drug producing the flag exited duration envelope",
    FlagClearReason.SPONTANEOUS_RESOLUTION: "Physiology model resolved without intervention",
    FlagClearReason.RESOLVED: "Pharmacodynamic model cleared the flag",
    FlagClearReason.CASE_END: "Case ended; residual flags force-cleared",
    FlagClearReason.INSTRUCTOR_CLEARED: "Instructor console manually cleared the flag",
}
