"""I-S-10 — evidence class enum for ledger / Dhaara confidence weighting."""
from __future__ import annotations

from enum import Enum
from typing import Final

from services.pratibimb.samvaad.metrics import (
    REASON_PATIENT_REPORTED,
    record_summative_rejected,
)
from services.pratibimb.samvaad.summative_contract import (
    ERROR_KIND_SUMMATIVE_EVIDENCE_CLASS_FORBIDDEN,
    SummativeEvidenceError,
    SummativeEvidenceRejectedError,
)


class EvidenceClass(str, Enum):
    """Pinned at Framework v1 freeze — additive-only; removals require v2."""

    MACHINE_SIM = "machine_sim"
    PRECEPTOR_ATTESTED = "preceptor_attested"
    PATIENT_REPORTED = "patient_reported"


KNOWN_EVIDENCE_CLASSES: Final[frozenset[str]] = frozenset(e.value for e in EvidenceClass)

SUMMATIVE_ELIGIBLE_CLASSES: Final[frozenset[str]] = frozenset(
    {
        EvidenceClass.MACHINE_SIM.value,
        EvidenceClass.PRECEPTOR_ATTESTED.value,
    }
)

CLASS_CONFIDENCE_WEIGHT: Final[dict[str, float]] = {
    EvidenceClass.MACHINE_SIM.value: 1.0,
    EvidenceClass.PRECEPTOR_ATTESTED.value: 1.35,
    EvidenceClass.PATIENT_REPORTED.value: 0.0,
}


def assert_known_evidence_class(value: str) -> None:
    if value not in KNOWN_EVIDENCE_CLASSES:
        raise ValueError(
            f"evidence_class {value!r} not in KNOWN_EVIDENCE_CLASSES; "
            "extend via Framework v2 pin"
        )


def summative_eligible(evidence_class: str) -> bool:
    assert_known_evidence_class(evidence_class)
    return evidence_class in SUMMATIVE_ELIGIBLE_CLASSES


def assert_summative_evidence_class(evidence_class: str) -> None:
    """Fail-closed under I-S-13: patient_reported not summative until Phase 4 lift."""
    assert_known_evidence_class(evidence_class)
    if evidence_class == EvidenceClass.PATIENT_REPORTED.value:
        record_summative_rejected(reason=REASON_PATIENT_REPORTED)
        raise SummativeEvidenceRejectedError(
            "I-S-13: evidence_class=patient_reported forbidden in summative "
            "credentialing until Phase 4 governance pin lifts",
            error_kind=ERROR_KIND_SUMMATIVE_EVIDENCE_CLASS_FORBIDDEN,
        )
    if not summative_eligible(evidence_class):
        raise SummativeEvidenceError(
            f"evidence_class {evidence_class!r} not summative-eligible"
        )
