"""
Evidence provenance vocabulary.

Lives in `shared/` because Dhaara consumes evidence kinds when updating a
learner's posterior and must not have to import a Pratibimb service module to
know how far to trust an observation.

`EvidenceKind` (who produced it) is deliberately separate from
`evaluator_version` (which build produced it). Folding provenance into a version
string like `nirikshak-v0.3` vs `preceptor-manual` would force every consumer to
parse strings to recover trust level. This is where the "Nirikshak produces
evidence, Dhaara interprets it" boundary becomes legible to the type system.
"""
from __future__ import annotations

from enum import Enum


class EvidenceKind(str, Enum):
    AUTO_RUBRIC = "auto_rubric"
    LLM_JUDGE = "llm_judge"
    PRECEPTOR = "preceptor"
    PEER = "peer"
    DRISHTI_CV = "drishti_cv"
    SELF_REPORT = "self_report"
    ERROR_DNA = "error_dna"


# Prior trust per provenance, consumed by Dhaara when updating the posterior.
# A deterministic rubric hit is checkable; a self-report is a claim.
EVIDENCE_KIND_PRIOR_WEIGHT: dict[EvidenceKind, float] = {
    EvidenceKind.PRECEPTOR: 1.00,
    EvidenceKind.AUTO_RUBRIC: 0.90,
    EvidenceKind.ERROR_DNA: 0.85,
    EvidenceKind.DRISHTI_CV: 0.70,
    EvidenceKind.LLM_JUDGE: 0.55,
    EvidenceKind.PEER: 0.35,
    EvidenceKind.SELF_REPORT: 0.15,
}


def prior_weight(kind: EvidenceKind) -> float:
    return EVIDENCE_KIND_PRIOR_WEIGHT[kind]
