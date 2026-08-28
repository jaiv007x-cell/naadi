"""
Nirikshak -- the case grader.

Consumes a `GradingBlueprint` plus the typed `shared.schemas.trace.PhysioTrace`
and emits a `CaseGrade`.

Two invariants hold everywhere in this module:

1. Positive-evidence only. A hit awards `points` when the desired behaviour is
   demonstrated and zero otherwise. Nothing subtracts. Criticality is expressed
   through `required` + `fail_case_on_violation`, not negative points.
2. No duck-typing. Every trace read goes through a typed query method.

Reproducibility: each grade carries an `evaluation_manifest_hash` over the
grader/rubric/physio versions, the rubric definition, and the canonical trace,
so a score can always be tied back to the exact inputs that produced it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable

from shared.schemas.trace import PhysioTrace

from .rubric import (
    NIRIKSHAK_VERSION,
    Axis,
    GradingBlueprint,
    RubricHit,
    Severity,
    letter_for,
)

from services.pratibimb.app.eval.matcher_registry import KNOWN_MATCHER_KINDS
from services.pratibimb.app.physio.version_probe import get_version as _get_physio_version
from services.pratibimb.app.eval.error_dna import compute_error_dna

CASE_START = "case_start"


class UngradableSessionError(RuntimeError):
    """
    Raised when a session that must produce a grade cannot produce one.

    Only summative sessions raise this. A silent `None` grade on a
    credential-bearing case is indistinguishable from a pass to everyone
    downstream, so the failure has to surface while a preceptor can still act
    on it.
    """

    def __init__(self, session_id: str, case_id: str, reason: str):
        self.session_id = session_id
        self.case_id = case_id
        self.reason = reason
        super().__init__(
            f"summative session {session_id!r} on case {case_id!r} is ungradable: {reason}"
        )


class MissingGradingBlueprint(UngradableSessionError):
    """
    The specific, and by far most common, way a summative session is ungradable.

    A subclass rather than a separate exception so `except UngradableSessionError`
    still catches every refusal to grade, while callers that want to distinguish
    "unauthored rubric" from "grader crashed" can.
    """

    def __init__(self, session_id: str, case_id: str, case_version: str = ""):
        self.case_version = case_version
        stamped = f"{case_id}@{case_version}" if case_version else case_id
        super().__init__(
            session_id, case_id,
            f"{stamped} is SUMMATIVE but has no grading_blueprint; "
            "refusing to silently skip grading",
        )


# ── outcome objects ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HitOutcome:
    hit_id: str
    matched: bool
    points_awarded: float
    evidence: dict
    axis: Axis
    severity: Severity


@dataclass(frozen=True)
class AxisScore:
    axis: Axis
    earned: float
    max_points: float
    normalized: float   # earned / max (0..1); 1.0 when the axis has no hits


@dataclass(frozen=True)
class CaseGrade:
    case_id: str
    case_version: str
    rubric_version: str
    grader_version: str
    physio_version: str
    outcomes: tuple[HitOutcome, ...]
    axis_scores: tuple[AxisScore, ...]
    overall: float
    letter: str
    critical_violations: tuple[str, ...]
    failed: bool
    evaluation_manifest_hash: str
    error_dna: dict | None = None

    @property
    def passed(self) -> bool:
        return not self.failed

    def axis_score(self, axis: Axis) -> AxisScore | None:
        return next((a for a in self.axis_scores if a.axis == axis), None)

    def outcome(self, hit_id: str) -> HitOutcome | None:
        return next((o for o in self.outcomes if o.hit_id == hit_id), None)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "case_version": self.case_version,
            "rubric_version": self.rubric_version,
            "grader_version": self.grader_version,
            "physio_version": self.physio_version,
            "overall": round(self.overall, 4),
            "letter": self.letter,
            "failed": self.failed,
            "critical_violations": list(self.critical_violations),
            "axis_scores": [
                {
                    "axis": a.axis.value,
                    "earned": round(a.earned, 3),
                    "max_points": round(a.max_points, 3),
                    "normalized": round(a.normalized, 4),
                }
                for a in self.axis_scores
            ],
            "outcomes": [
                {
                    "hit_id": o.hit_id,
                    "matched": o.matched,
                    "points_awarded": round(o.points_awarded, 3),
                    "axis": o.axis.value,
                    "severity": o.severity.value,
                    "evidence": o.evidence,
                }
                for o in self.outcomes
            ],
            "evaluation_manifest_hash": self.evaluation_manifest_hash,
        }


# ── grader context ───────────────────────────────────────────────────────────

class GraderContext:
    """Thin adapter over PhysioTrace that resolves timing references."""

    def __init__(self, trace: PhysioTrace):
        self.trace = trace

    def reference_time(self, ref: str | None) -> float | None:
        """
        Resolve a timing anchor to seconds.

        `None`, `""` and `"case_start"` all mean "start of the case" (0.0).
        Any other value names a flag; if that flag never fired, the anchor is
        unresolvable and the caller must treat the hit as unmatched.
        """
        if ref in (None, "", CASE_START):
            return 0.0
        return self.trace.flag_set_at(ref)


Matcher = Callable[["Nirikshak", RubricHit], "tuple[bool, dict]"]


# ── evidence helpers ─────────────────────────────────────────────────────────

_TIME_KEYS = ("admin", "order", "dx", "escalation", "handoff", "recognition")


def _extract_time(evidence: dict) -> float | None:
    """Best-effort timestamp of the thing an evidence dict describes."""
    if not isinstance(evidence, dict):
        return None
    if "t_s" in evidence and isinstance(evidence["t_s"], (int, float)):
        return float(evidence["t_s"])
    for key in _TIME_KEYS:
        inner = evidence.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("t_s"), (int, float)):
            return float(inner["t_s"])
    events = evidence.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict) and isinstance(first.get("t_s"), (int, float)):
            return float(first["t_s"])
    chain = evidence.get("chain")
    if isinstance(chain, list) and chain:
        last = chain[-1]
        if isinstance(last, dict) and isinstance(last.get("t_s"), (int, float)):
            return float(last["t_s"])
    return None


def _timing_ok(
    t_s: float, ref_t: float, within_s: float | None
) -> bool:
    if t_s < ref_t:
        return False
    return within_s is None or (t_s - ref_t) <= within_s


# ── the grader ───────────────────────────────────────────────────────────────

_MATCHERS: dict[str, Matcher] = {}


class Nirikshak:
    def __init__(self, blueprint: GradingBlueprint, trace: PhysioTrace):
        self.bp = blueprint
        self.trace = trace
        self.ctx = GraderContext(trace)
        self._matchers = _MATCHERS

    # ── matchers ─────────────────────────────────────────────────────────

    def _m_drug_given(self, hit: RubricHit) -> tuple[bool, dict]:
        p = hit.params
        drug_id = p["drug_id"]
        admins = self.trace.drug_admins(drug_id)
        ref_t = self.ctx.reference_time(p.get("after_flag"))
        if ref_t is None:
            return False, {"drug_id": drug_id, "reason": "anchor_flag_never_set",
                           "after_flag": p.get("after_flag")}

        within = p.get("within_s")
        for a in admins:
            if "route" in p and a.route.upper() != str(p["route"]).upper():
                continue
            if "min_dose" in p and a.dose < p["min_dose"]:
                continue
            if "max_dose" in p and a.dose > p["max_dose"]:
                continue
            if not _timing_ok(a.t_s, ref_t, within):
                continue
            return True, {"admin": asdict(a)}
        return False, {"drug_id": drug_id, "admins_seen": [asdict(a) for a in admins]}

    def _m_drug_not_given(self, hit: RubricHit) -> tuple[bool, dict]:
        """
        Positive semantics: matched=True means the contraindicated drug was
        correctly withheld, i.e. safe behaviour was demonstrated.

        `while_flag` narrows the check to the spans in which that flag was
        active, so "no beta-blocker during anaphylaxis" is expressible without
        forbidding the drug for the whole case.
        """
        p = hit.params
        drug_id = p["drug_id"]
        admins = self.trace.drug_admins(drug_id)
        if "route" in p:
            admins = [a for a in admins if a.route.upper() == str(p["route"]).upper()]

        while_flag = p.get("while_flag")
        if while_flag:
            spans = self.trace.flag_spans(while_flag)
            admins = [
                a for a in admins
                if any(lo <= a.t_s <= hi for lo, hi in spans)
            ]

        withheld = not admins
        return withheld, {
            "drug_id": drug_id,
            "while_flag": while_flag,
            "violations": [asdict(a) for a in admins],
        }

    def _m_order_placed(self, hit: RubricHit) -> tuple[bool, dict]:
        p = hit.params
        order_id = p["order_id"]
        orders = self.trace.orders(order_id)
        ref_t = self.ctx.reference_time(p.get("after_flag"))
        if ref_t is None:
            return False, {"order_id": order_id, "reason": "anchor_flag_never_set",
                           "after_flag": p.get("after_flag")}

        within = p.get("within_s")
        constraints = p.get("params", {})
        for o in orders:
            if not _timing_ok(o.t_s, ref_t, within):
                continue
            if constraints and not _params_satisfied(o.params, constraints):
                continue
            return True, {"order": asdict(o)}
        return False, {"order_id": order_id, "orders_seen": [asdict(o) for o in orders]}

    def _m_diagnosis_stated(self, hit: RubricHit) -> tuple[bool, dict]:
        p = hit.params
        targets = {p["dx"].lower(), *(a.lower() for a in p.get("aliases", ()))}
        min_conf = p.get("min_confidence", 0.5)
        ref_t = self.ctx.reference_time(p.get("after_flag"))
        if ref_t is None:
            return False, {"dx": p["dx"], "reason": "anchor_flag_never_set"}
        within = p.get("within_s")

        seen = []
        for d in self.trace.diagnoses():
            name = d.dx.lower()
            if not (name in targets or any(t in name for t in targets)):
                continue
            seen.append(asdict(d))
            if d.confidence < min_conf:
                continue
            if not _timing_ok(d.t_s, ref_t, within):
                continue
            return True, {"dx": asdict(d)}
        return False, {"dx": p["dx"], "seen": seen}

    def _m_vital_recognized(self, hit: RubricHit) -> tuple[bool, dict]:
        return self._recognition(hit, "vital")

    def _m_finding_recognized(self, hit: RubricHit) -> tuple[bool, dict]:
        return self._recognition(hit, "finding")

    def _m_symptom_elicited(self, hit: RubricHit) -> tuple[bool, dict]:
        return self._recognition(hit, "symptom")

    def _m_physical_sign(self, hit: RubricHit) -> tuple[bool, dict]:
        return self._recognition(hit, "physical_sign")

    def _recognition(self, hit: RubricHit, kind: str) -> tuple[bool, dict]:
        p = hit.params
        token = p["token"]
        events = self.trace.recognitions(kind=kind, token=token)
        ref_t = self.ctx.reference_time(p.get("after_flag"))
        if ref_t is None:
            return False, {"token": token, "reason": "anchor_flag_never_set"}
        within = p.get("within_s")
        for r in events:
            if _timing_ok(r.t_s, ref_t, within):
                return True, {"recognition": asdict(r)}
        return False, {"token": token, "kind": kind,
                       "events": [asdict(r) for r in events]}

    def _m_escalation(self, hit: RubricHit) -> tuple[bool, dict]:
        p = hit.params
        target = p.get("target")
        escalations = self.trace.escalations(target)
        ref_t = self.ctx.reference_time(p.get("after_flag"))
        if ref_t is None:
            return False, {"target": target, "reason": "anchor_flag_never_set"}
        within = p.get("within_s")
        for e in escalations:
            if _timing_ok(e.t_s, ref_t, within):
                return True, {"escalation": asdict(e)}
        return False, {"target": target,
                       "escalations_seen": [asdict(e) for e in escalations]}

    def _m_handoff_given(self, hit: RubricHit) -> tuple[bool, dict]:
        """SBAR-style handoff: every name in `required_fields` must be present."""
        p = hit.params
        required = {f.lower() for f in p.get("required_fields", ())}
        target = p.get("target")
        ref_t = self.ctx.reference_time(p.get("after_flag"))
        if ref_t is None:
            return False, {"reason": "anchor_flag_never_set"}
        within = p.get("within_s")

        seen = []
        for h in self.trace.handoffs(target):
            provided = {f.lower() for f in h.fields}
            seen.append({"fields": sorted(provided), "target": h.target, "t_s": h.t_s})
            if required and not required.issubset(provided):
                continue
            if not _timing_ok(h.t_s, ref_t, within):
                continue
            return True, {"handoff": {"fields": sorted(provided),
                                      "target": h.target, "t_s": h.t_s}}
        return False, {"required_fields": sorted(required), "handoffs_seen": seen}

    def _m_sequence(self, hit: RubricHit) -> tuple[bool, dict]:
        """
        params: {"steps": [<hit_id>, ...], "max_gap_s": float | None}

        Each step must match its own matcher and occur no earlier than the
        previous step. `max_gap_s` bounds the spacing between consecutive steps.
        """
        step_ids: list[str] = list(hit.params["steps"])
        max_gap = hit.params.get("max_gap_s")
        last_t = -1.0
        chain: list[dict] = []

        for sid in step_ids:
            sub = self.bp.hit(sid)
            if sub is None:
                return False, {"error": f"unknown step {sid}"}
            matcher = self._matchers.get(sub.matcher)
            if matcher is None:
                return False, {"error": f"unknown matcher {sub.matcher}", "step": sid}

            matched, ev = matcher(self, sub)
            if not matched:
                return False, {"failed_at": sid, "evidence": ev}

            t = _extract_time(ev)
            if t is None:
                return False, {"failed_at": sid, "reason": "untimed_evidence",
                               "evidence": ev}
            if t < last_t:
                return False, {"failed_at": sid, "reason": "out_of_order",
                               "evidence": ev}
            if max_gap is not None and last_t >= 0.0 and (t - last_t) > max_gap:
                return False, {"failed_at": sid, "reason": "gap_exceeded",
                               "gap_s": t - last_t, "max_gap_s": max_gap}
            last_t = t
            chain.append({"id": sid, "t_s": t})

        return True, {"chain": chain}

    def _m_action_within(self, hit: RubricHit) -> tuple[bool, dict]:
        """
        Timing wrapper around another hit.
        params: {"inner_hit": id, "within_s": N, "after_flag": str | None}
        """
        p = hit.params
        inner = self.bp.hit(p["inner_hit"])
        if inner is None:
            return False, {"error": f"unknown inner_hit {p['inner_hit']}"}
        matcher = self._matchers.get(inner.matcher)
        if matcher is None:
            return False, {"error": f"unknown matcher {inner.matcher}"}

        inner_params = dict(inner.params)
        inner_params["within_s"] = p["within_s"]
        inner_params["after_flag"] = p.get("after_flag", CASE_START)
        proxy = replace(inner, params=inner_params)
        return matcher(self, proxy)

    def _m_flag_present(self, hit: RubricHit) -> tuple[bool, dict]:
        p = hit.params
        flag = p["flag"]
        t = self.trace.flag_set_at(flag)
        if t is None:
            return False, {"flag": flag}
        within = p.get("within_s")
        ref_t = self.ctx.reference_time(p.get("after_flag"))
        if ref_t is None:
            return False, {"flag": flag, "reason": "anchor_flag_never_set"}
        if not _timing_ok(t, ref_t, within):
            return False, {"flag": flag, "t_s": t, "reason": "outside_window"}
        return True, {"flag": flag, "t_s": t}

    def _m_flag_cleared(self, hit: RubricHit) -> tuple[bool, dict]:
        p = hit.params
        flag = p["flag"]
        t = self.trace.flag_cleared_at(flag)
        if t is None:
            return False, {"flag": flag}
        within = p.get("within_s")
        ref_t = self.ctx.reference_time(p.get("after_flag"))
        if ref_t is None:
            return False, {"flag": flag, "reason": "anchor_flag_never_set"}
        if not _timing_ok(t, ref_t, within):
            return False, {"flag": flag, "t_s": t, "reason": "outside_window"}
        return True, {"flag": flag, "t_s": t}

    # ── grading ──────────────────────────────────────────────────────────

    def grade(self) -> CaseGrade:
        outcomes: list[HitOutcome] = []
        critical: list[str] = []

        for hit in self.bp.hits:
            matcher = self._matchers.get(hit.matcher)
            if matcher is None:
                matched, evidence = False, {"error": f"unknown matcher {hit.matcher}"}
            else:
                matched, evidence = matcher(self, hit)

            points = hit.points if matched else 0.0
            if not matched and hit.required and hit.fail_case_on_violation:
                critical.append(hit.id)

            outcomes.append(HitOutcome(
                hit_id=hit.id,
                matched=matched,
                points_awarded=points,
                evidence=evidence,
                axis=hit.axis,
                severity=hit.severity,
            ))

        axis_scores: list[AxisScore] = []
        for axis in Axis:
            earned = sum(o.points_awarded for o in outcomes if o.axis == axis)
            max_points = self.bp.max_points(axis)
            normalized = (earned / max_points) if max_points > 0 else 1.0
            axis_scores.append(AxisScore(
                axis=axis,
                earned=earned,
                max_points=max_points,
                normalized=normalized,
            ))

        overall = sum(
            a.normalized * self.bp.weights.get(a.axis, 0.0) for a in axis_scores
        )
        letter = letter_for(overall, self.bp.letter_bands)
        failed = bool(critical) or overall < self.bp.pass_threshold

        expected_seq = [
            h.params.get("drug", h.params.get("order_id", h.params.get("target", h.id)))
            for h in self.bp.hits
            if h.required
        ]
        dna = compute_error_dna(
            self.trace,
            expected_sequence=expected_seq,
            expected_doses={
                h.params["drug"]: h.params["dose_mg"]
                for h in self.bp.hits
                if "drug" in h.params and "dose_mg" in h.params
            } or None,
            time_pressure_s=900.0,
        )

        return CaseGrade(
            case_id=self.bp.case_id,
            case_version=self.bp.case_version,
            rubric_version=self.bp.rubric_version,
            grader_version=NIRIKSHAK_VERSION,
            physio_version=_get_physio_version(),
            outcomes=tuple(outcomes),
            axis_scores=tuple(axis_scores),
            overall=overall,
            letter=letter,
            critical_violations=tuple(critical),
            failed=failed,
            evaluation_manifest_hash=self.evaluation_manifest_hash(),
            error_dna=dna.to_dict(),
        )

    # ── reproducibility ──────────────────────────────────────────────────

    def evaluation_manifest(self) -> dict:
        """
        Everything that determined the score. Pinning the rubric definition (not
        just its version string) means an edited rubric yields a different hash
        even if someone forgets to bump `rubric_version`.
        """
        return {
            "case_id": self.bp.case_id,
            "case_version": self.bp.case_version,
            "rubric_version": self.bp.rubric_version,
            "grader_version": NIRIKSHAK_VERSION,
            "physio_version": _get_physio_version(),
            "rubric": self.bp.canonical_dict(),
            "trace": self.trace.to_canonical(),
        }

    def evaluation_manifest_hash(self) -> str:
        blob = json.dumps(
            self.evaluation_manifest(), sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _params_satisfied(actual: dict[str, Any], constraints: dict[str, Any]) -> bool:
    """`foo_min`/`foo_max` compare numerically against `foo`; others are equality."""
    for key, expected in constraints.items():
        if key.endswith("_min"):
            base = key[: -len("_min")]
            if actual.get(base, -float("inf")) < expected:
                return False
        elif key.endswith("_max"):
            base = key[: -len("_max")]
            if actual.get(base, float("inf")) > expected:
                return False
        elif actual.get(key) != expected:
            return False
    return True


_MATCHERS = {
    "drug_given": Nirikshak._m_drug_given,
    "drug_not_given": Nirikshak._m_drug_not_given,
    "order_placed": Nirikshak._m_order_placed,
    "diagnosis_stated": Nirikshak._m_diagnosis_stated,
    "vital_recognized": Nirikshak._m_vital_recognized,
    "finding_recognized": Nirikshak._m_finding_recognized,
    "symptom_elicited": Nirikshak._m_symptom_elicited,
    "physical_sign_identified": Nirikshak._m_physical_sign,
    "escalation": Nirikshak._m_escalation,
    "handoff_given": Nirikshak._m_handoff_given,
    "sequence": Nirikshak._m_sequence,
    "action_within": Nirikshak._m_action_within,
    "flag_present": Nirikshak._m_flag_present,
    "flag_cleared": Nirikshak._m_flag_cleared,
}

from services.pratibimb.samvaad.registry import apply_samvaad_registry_merge
from services.pratibimb.samvaad.nirikshak_matchers import SAMVAAD_MATCHERS

apply_samvaad_registry_merge()
from services.pratibimb.app.eval import matcher_registry as _matcher_registry

_MATCHERS.update(SAMVAAD_MATCHERS)

if set(_MATCHERS) != _matcher_registry.KNOWN_MATCHER_KINDS:
    raise RuntimeError(
        "matcher_registry.KNOWN_MATCHER_KINDS drifted from Nirikshak implementations: "
        f"registry_only={sorted(_matcher_registry.KNOWN_MATCHER_KINDS - set(_MATCHERS))} "
        f"nirikshak_only={sorted(set(_MATCHERS) - _matcher_registry.KNOWN_MATCHER_KINDS)}"
    )
