"""
FlagCause — closed vocabulary of reasons a physiologic flag can be set or cleared.

Namespaced with dotted paths so the ledger read API can cheaply prefix-filter
(e.g. WHERE cause LIKE 'drug.%').

No FlagCause.OTHER. No free-text escape hatch. If a callsite needs a new cause,
add it here explicitly and update rubric consumers.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Final

_CAUSE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class FlagCause(str, Enum):
    # ── drug.* ──────────────────────────────────────────────────────────
    DRUG_ADMIN = "drug.admin"
    DRUG_ALLERGY = "drug.allergy"
    DRUG_CONTRAINDICATED = "drug.contraindicated"
    DRUG_PD_EFFECT = "drug.pd_effect"
    DRUG_PK_CLEARANCE = "drug.pk_clearance"
    DRUG_TOXIC = "drug.toxic"
    DRUG_REVERSAL = "drug.reversal"
    DRUG_INTERACTION = "drug.interaction"

    # ── physiology.* ────────────────────────────────────────────────────
    PHYSIOLOGY_HOMEOSTASIS = "physiology.homeostasis"
    PHYSIOLOGY_COMPENSATION = "physiology.compensation"
    PHYSIOLOGY_DECOMPENSATION = "physiology.decompensation"
    PHYSIOLOGY_TIMEOUT = "physiology.timeout"
    PHYSIOLOGY_PHARMACODYNAMIC = "physiology.pharmacodynamic"

    # ── clinical.* ──────────────────────────────────────────────────────
    CLINICAL_SEPSIS_SUSPECTED = "clinical.sepsis_suspected"
    CLINICAL_HYPOXIA = "clinical.hypoxia"
    CLINICAL_HYPOTENSION = "clinical.hypotension"
    CLINICAL_HYPERTENSION = "clinical.hypertension"
    CLINICAL_ARRHYTHMIA = "clinical.arrhythmia"
    CLINICAL_ANAPHYLAXIS_ONSET = "clinical.anaphylaxis_onset"
    CLINICAL_AIRWAY_COMPROMISE = "clinical.airway_compromise"
    CLINICAL_ALTERED_MENTATION = "clinical.altered_mentation"

    # ── procedure.* ─────────────────────────────────────────────────────
    PROCEDURE_STARTED = "procedure.started"
    PROCEDURE_COMPLETED = "procedure.completed"
    PROCEDURE_FAILED = "procedure.failed"
    PROCEDURE_COMPLICATION = "procedure.complication"

    # ── case.* ──────────────────────────────────────────────────────────
    CASE_START = "case.start"
    CASE_END = "case.end"
    CASE_PERTURBATION = "case.perturbation"
    CASE_INSTRUCTOR_INJECT = "case.instructor_inject"

    # ── learner.* ───────────────────────────────────────────────────────
    LEARNER_RECOGNITION = "learner.recognition"
    LEARNER_ORDER = "learner.order"
    LEARNER_HANDOFF = "learner.handoff"
    LEARNER_ESCALATION = "learner.escalation"

    @classmethod
    def validate_registered(cls) -> None:
        """
        Startup invariant: every value matches the dotted-namespace regex.
        Called by PhysiologyEngine.__init__ so a malformed enum value fails
        loud at boot, not silently in the ledger.
        """
        bad: list[str] = []
        for member in cls:
            if not _CAUSE_PATTERN.match(member.value):
                bad.append(f"{member.name}={member.value!r}")
        if bad:
            raise RuntimeError(
                "FlagCause has malformed values (must match "
                f"{_CAUSE_PATTERN.pattern}): {', '.join(bad)}"
            )

    @classmethod
    def namespace(cls, member: "FlagCause") -> str:
        """Return the top-level namespace, e.g. 'drug' for DRUG_ADMIN."""
        return member.value.split(".", 1)[0]
