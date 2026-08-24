"""
Grading blueprint contract.

Lives in `shared/` because `CaseBlueprintV2` embeds a blueprint and the case
object cannot depend on a service package. `services.pratibimb.app.eval.rubric`
re-exports everything here, so existing grader imports keep working.

Positive-evidence semantics are enforced by construction: a `RubricHit` awards
`points` when the desired behaviour is demonstrated and zero otherwise. There is
no negative-points path. Criticality is expressed with `required` plus
`fail_case_on_violation`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

NIRIKSHAK_VERSION = "0.3.0"


class Axis(str, Enum):
    DIAGNOSTIC = "diagnostic"
    ACTION = "action"
    TIMING = "timing"
    SAFETY = "safety"
    COMMUNICATION = "communication"


class Severity(str, Enum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


@dataclass(frozen=True)
class RubricHit:
    """
    Positive-evidence semantics ONLY.

    `matched=True`  -> desired competency demonstrated -> award `points`.
    `matched=False` -> not demonstrated -> award 0. If `required` and
                       `fail_case_on_violation`, adds to critical_violations.

    Safety rules must express DESIRED SAFE BEHAVIOUR (e.g.
    "contraindicated nitrate withheld"), never a penalty.
    """

    id: str
    axis: Axis
    matcher: str
    params: dict = field(default_factory=dict)
    points: float = 1.0
    severity: Severity = Severity.MINOR
    required: bool = False
    fail_case_on_violation: bool = False
    description: str = ""
    competencies: tuple[str, ...] = ()

    def canonical_dict(self) -> dict:
        """Deterministic form; pins rule content into evaluation manifests."""
        return {
            "id": self.id,
            "axis": self.axis.value,
            "matcher": self.matcher,
            "params": _canonical(self.params),
            "points": self.points,
            "severity": self.severity.value,
            "required": self.required,
            "fail_case_on_violation": self.fail_case_on_violation,
            "competencies": list(self.competencies),
        }


_DEFAULT_WEIGHTS: dict[Axis, float] = {
    Axis.DIAGNOSTIC: 0.30,
    Axis.ACTION: 0.25,
    Axis.TIMING: 0.15,
    Axis.SAFETY: 0.20,
    Axis.COMMUNICATION: 0.10,
}

_DEFAULT_BANDS: tuple[tuple[float, str], ...] = (
    (0.90, "A"), (0.80, "B"), (0.70, "C"), (0.60, "D"), (0.0, "F"),
)


@dataclass(frozen=True)
class GradingBlueprint:
    """Grader-only view of a case: which behaviours earn credit and how much."""

    case_id: str
    case_version: str
    rubric_version: str
    hits: tuple[RubricHit, ...]
    display_name: str = ""
    weights: dict[Axis, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    letter_bands: tuple[tuple[float, str], ...] = _DEFAULT_BANDS
    pass_threshold: float = 0.70

    def hits_by_axis(self, axis: Axis) -> list[RubricHit]:
        return [h for h in self.hits if h.axis == axis]

    def max_points(self, axis: Axis) -> float:
        """All hits are positive-evidence, so nothing is excluded from the max."""
        return sum(h.points for h in self.hits_by_axis(axis))

    def hit(self, hit_id: str) -> RubricHit | None:
        return next((h for h in self.hits if h.id == hit_id), None)

    def canonical_dict(self) -> dict:
        """
        Deterministic serialization of the whole blueprint.

        Consumed by both the evaluation manifest hash and
        `CaseBlueprintV2.compute_content_hash()`, so an edited rule changes both
        hashes even if nobody remembers to bump `rubric_version`.
        """
        return {
            "case_id": self.case_id,
            "case_version": self.case_version,
            "rubric_version": self.rubric_version,
            "display_name": self.display_name,
            "hits": [h.canonical_dict() for h in self.hits],
            "weights": {a.value: w for a, w in sorted(
                self.weights.items(), key=lambda kv: kv[0].value
            )},
            "letter_bands": [[t, letter] for t, letter in self.letter_bands],
            "pass_threshold": self.pass_threshold,
        }


def letter_for(score: float, bands: tuple[tuple[float, str], ...] = _DEFAULT_BANDS) -> str:
    for threshold, letter in bands:
        if score >= threshold:
            return letter
    return bands[-1][1] if bands else "F"


def _canonical(value):
    """Recursively normalize params into JSON-stable primitives."""
    if isinstance(value, dict):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical(v) for v in value)
    if isinstance(value, Enum):
        return value.value
    return value
