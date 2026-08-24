"""
CaseBlueprintV2 — the frozen case contract.

Everything downstream keys off this object, so it is deliberately decomposed
into named sub-specs rather than a flat bag of fields: the Evidence Ledger's
foreign keys, the scenario compiler's inputs, and Dhaara's posterior all need to
reference stable sub-structures.

Two properties matter most:

- `assessment_mode` lives on `identity`, so how much a grade counts is a
  property of the case rather than of whoever happened to launch the session.
- `compute_content_hash()` covers the clinical substance and the grading
  blueprint but excludes provenance, so re-review of unchanged content does not
  invalidate the hash while any edit to clinical meaning does.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from shared.schemas.case import Demographics, Language, Vital
from shared.schemas.grading import GradingBlueprint
from shared.schemas.session import AssessmentMode

__all__ = [
    "AssessmentMode",
    "CorpusTier",
    "CaseIdentity",
    "CaseTargeting",
    "PatientPersona",
    "ClinicalTruth",
    "EnvironmentSpec",
    "PhysiologySpec",
    "InteractionSpec",
    "Provenance",
    "CaseBlueprintV2",
]

CASE_SCHEMA_VERSION = "2.0.0"


class CorpusTier(str, Enum):
    """
    Corpus readiness, derived from provenance completeness rather than asserted.

    Only GOLD is defensible as summative material: it requires a named clinical
    reviewer, a grading blueprint, and pinned guideline versions.
    """

    GOLD = "gold"
    SILVER = "silver"
    BRONZE = "bronze"
    DRAFT = "draft"

    @property
    def summative_eligible(self) -> bool:
        return self is CorpusTier.GOLD


# ── Identity ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CaseIdentity:
    case_id: str
    version: str
    title: str
    corpus_tier: CorpusTier
    assessment_mode: AssessmentMode


# ── Targeting ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CaseTargeting:
    roles: tuple[str, ...]
    competencies: tuple[str, ...]
    difficulty: float
    prerequisites: tuple[str, ...] = ()


# ── Patient ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PatientPersona:
    demographics: Demographics
    language: Language
    voice_profile: str
    literacy_years: int
    persona_notes: str = ""


# ── Clinical truth ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ClinicalTruth:
    primary_diagnosis: str
    icd10: str
    differentials: tuple[str, ...]
    symptoms_present: tuple[str, ...]
    findings_present: tuple[str, ...]
    absent_findings: tuple[str, ...]
    allergies: tuple[str, ...]
    medications: tuple[str, ...]
    onset_minutes_ago: int


# ── Environment ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EnvironmentSpec:
    setting: str
    resources: tuple[str, ...]
    distractors: tuple[str, ...] = ()
    difficulty_parameters: dict = field(default_factory=dict)


# ── Physiology ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PhysiologySpec:
    engine_version: str
    initial_vitals: Vital
    deterioration_rules: tuple[str, ...] = ()
    pharmacology_profile: str = "default"


# ── Interaction ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InteractionSpec:
    allowed_disclosures: tuple[str, ...]
    hidden_facts: tuple[str, ...]
    family_personas: tuple[str, ...] = ()
    dialogue_constraints: tuple[str, ...] = ()


# ── Provenance ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Provenance:
    author: str
    clinical_reviewer: Optional[str]
    sources: tuple[str, ...]
    guideline_versions: tuple[str, ...]
    reviewed_at: Optional[datetime]
    content_hash: str

    @property
    def is_clinically_reviewed(self) -> bool:
        return bool(self.clinical_reviewer) and self.reviewed_at is not None


# ── The case object ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CaseBlueprintV2:
    identity: CaseIdentity
    targeting: CaseTargeting
    patient: PatientPersona
    clinical_truth: ClinicalTruth
    environment: EnvironmentSpec
    physiology: PhysiologySpec
    interaction: InteractionSpec
    grading_blueprint: Optional[GradingBlueprint]
    provenance: Provenance
    schema_version: str = CASE_SCHEMA_VERSION
    probe_only: bool = False

    # ── convenience accessors ────────────────────────────────────────────

    @property
    def case_id(self) -> str:
        return self.identity.case_id

    @property
    def version(self) -> str:
        return self.identity.version

    @property
    def assessment_mode(self) -> AssessmentMode:
        return self.identity.assessment_mode

    @property
    def corpus_tier(self) -> CorpusTier:
        return self.identity.corpus_tier

    @property
    def is_gradable(self) -> bool:
        return self.grading_blueprint is not None

    # ── integrity ────────────────────────────────────────────────────────

    _HASHED_SECTIONS = (
        "identity", "targeting", "patient", "clinical_truth",
        "environment", "physiology", "interaction",
    )

    def compute_content_hash(self) -> str:
        """
        Deterministic hash of clinical substance plus grading rules.

        Excludes `provenance` entirely, including `provenance.content_hash`
        itself: re-review or re-attribution of unchanged content must not change
        the hash, while any edit to what the case actually asserts must.
        """
        payload = {name: _canonical(getattr(self, name)) for name in self._HASHED_SECTIONS}
        payload["grading_blueprint"] = (
            self.grading_blueprint.canonical_dict() if self.grading_blueprint else None
        )
        payload["schema_version"] = self.schema_version
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def content_hash_matches(self) -> bool:
        """False when the recorded hash has drifted from the current content."""
        return self.provenance.content_hash == self.compute_content_hash()

    def with_content_hash(self) -> CaseBlueprintV2:
        """Return a copy whose provenance records the current content hash."""
        from dataclasses import replace

        return replace(
            self,
            provenance=replace(self.provenance, content_hash=self.compute_content_hash()),
        )

    # ── validation ───────────────────────────────────────────────────────

    def validation_errors(self) -> list[str]:
        """
        Structural problems that should block promotion, worst first.

        Summative eligibility is the strict case: a case that stakes a
        credential needs a blueprint, a named reviewer, and GOLD tier.
        """
        errors: list[str] = []
        if not self.identity.case_id:
            errors.append("identity.case_id is empty")
        if not self.identity.version:
            errors.append("identity.version is empty")
        if not 0.0 <= self.targeting.difficulty <= 1.0:
            errors.append(
                f"targeting.difficulty {self.targeting.difficulty} outside [0, 1]"
            )
        if not self.clinical_truth.primary_diagnosis:
            errors.append("clinical_truth.primary_diagnosis is empty")

        if self.assessment_mode is AssessmentMode.SUMMATIVE:
            if self.grading_blueprint is None:
                errors.append("summative case has no grading_blueprint")
            if not self.provenance.is_clinically_reviewed:
                errors.append("summative case has no clinical reviewer")
            if not self.corpus_tier.summative_eligible:
                errors.append(
                    f"summative case is tier {self.corpus_tier.value}, not gold"
                )
        return errors

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors()

    def to_dict(self) -> dict:
        payload = {name: _canonical(getattr(self, name)) for name in self._HASHED_SECTIONS}
        payload["grading_blueprint"] = (
            self.grading_blueprint.canonical_dict() if self.grading_blueprint else None
        )
        payload["provenance"] = _canonical(self.provenance)
        payload["schema_version"] = self.schema_version
        return payload


def _canonical(obj):
    """
    Recursively normalize into JSON-stable primitives.

    Hand-rolled rather than `dataclasses.asdict` because the sub-specs embed
    pydantic models (`Demographics`, `Vital`), which `asdict` deep-copies
    verbatim instead of converting — leaving objects that only serialize via
    `repr()` and therefore hash unstably across versions.
    """
    if isinstance(obj, BaseModel):
        return _canonical(obj.model_dump(mode="json"))
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _canonical(getattr(obj, f.name)) for f in sorted(
            fields(obj), key=lambda f: f.name
        )}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _canonical(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, (set, frozenset)):
        return sorted(_canonical(v) for v in obj)
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    return obj
