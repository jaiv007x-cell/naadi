"""
Evidence Ledger — structured record of everything the grader considers.

The ledger is built from the session's PhysioTrace, Turn list, and CaseBlueprint
and provides a normalized view that the scorer can query without re-parsing raw logs.

Key design decisions:
- Each evidence item has a timestamp, category, provenance kind, and payload
- Categories map 1:1 to rubric dimensions (clinical, communication, procedural, pharmacological)
- `EvidenceKind` records WHO produced an observation, separately from
  `evaluator_version` which records WHICH build produced it. Downstream trust
  weighting depends on the former and must not have to parse the latter.
- Pharmacological evidence items carry timing windows that the scorer uses for
  "was the drug given in the right window?" checks
- Immutable once built; scorer reads but never writes
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from shared.schemas.evidence import (
    EVIDENCE_KIND_PRIOR_WEIGHT,
    EvidenceKind,
)


class EvidenceCategory(str, Enum):
    """What competency area a piece of evidence speaks to."""

    CLINICAL = "clinical"
    COMMUNICATION = "communication"
    PROCEDURAL = "procedural"
    PHARMACOLOGICAL = "pharmacological"
    SAFETY = "safety"
    HALLUCINATION = "hallucination"


@dataclass(frozen=True)
class EvidenceItem:
    t: float
    category: EvidenceCategory
    tag: str
    payload: dict[str, Any] = field(default_factory=dict)
    score_impact: float = 0.0
    kind: EvidenceKind = EvidenceKind.AUTO_RUBRIC
    evaluator_version: str = ""

    @property
    def prior_weight(self) -> float:
        return EVIDENCE_KIND_PRIOR_WEIGHT[self.kind]


@dataclass
class TimingWindow:
    """Expected timing for a critical action relative to session start."""
    action: str
    ideal_before_s: float
    acceptable_before_s: float
    actual_s: float | None = None

    @property
    def met_ideal(self) -> bool:
        return self.actual_s is not None and self.actual_s <= self.ideal_before_s

    @property
    def met_acceptable(self) -> bool:
        return self.actual_s is not None and self.actual_s <= self.acceptable_before_s

    @property
    def missed(self) -> bool:
        return self.actual_s is None


@dataclass
class DrugAdminEvidence:
    drug: str
    dose_mg: float
    route: str
    t_admin_s: float
    was_indicated: bool = True
    was_contraindicated: bool = False
    was_toxic: bool = False
    was_interaction: bool = False
    interaction_with: str | None = None
    was_reversed: bool = False
    reversal_drug: str | None = None
    plasma_peak_reached: bool = False


@dataclass
class CrisisEvidence:
    t_start_s: float
    t_end_s: float | None = None
    trigger: str = ""
    learner_responded: bool = False
    response_latency_s: float | None = None
    appropriate_action: bool = False


@dataclass
class EvidenceLedger:
    """Complete evidence record for one session."""

    items: list[EvidenceItem] = field(default_factory=list)
    timing_windows: list[TimingWindow] = field(default_factory=list)
    drug_admins: list[DrugAdminEvidence] = field(default_factory=list)
    crises: list[CrisisEvidence] = field(default_factory=list)
    session_duration_s: float = 0.0
    patient_died: bool = False
    evaluator_version: str = ""

    def add(self, t: float, category: EvidenceCategory, tag: str,
            payload: dict | None = None, score_impact: float = 0.0,
            kind: EvidenceKind = EvidenceKind.AUTO_RUBRIC,
            evaluator_version: str = "") -> None:
        self.items.append(EvidenceItem(
            t=t, category=category, tag=tag,
            payload=payload or {}, score_impact=score_impact,
            kind=kind, evaluator_version=evaluator_version or self.evaluator_version,
        ))

    def items_by_category(self, cat: EvidenceCategory) -> list[EvidenceItem]:
        return [i for i in self.items if i.category == cat]

    def items_by_kind(self, kind: EvidenceKind) -> list[EvidenceItem]:
        return [i for i in self.items if i.kind == kind]

    def kinds_present(self) -> set[EvidenceKind]:
        return {i.kind for i in self.items}

    def total_impact(self, cat: EvidenceCategory) -> float:
        return sum(i.score_impact for i in self.items if i.category == cat)

    def weighted_impact(self, cat: EvidenceCategory) -> float:
        """Score impact discounted by each item's provenance weight."""
        return sum(
            i.score_impact * i.prior_weight for i in self.items if i.category == cat
        )

    def drugs_given(self) -> set[str]:
        return {d.drug for d in self.drug_admins}

    def adverse_events(self) -> list[DrugAdminEvidence]:
        return [d for d in self.drug_admins if d.was_contraindicated or d.was_toxic or d.was_interaction]

    def timing_met_ratio(self) -> float:
        if not self.timing_windows:
            return 1.0
        met = sum(1 for tw in self.timing_windows if tw.met_acceptable)
        return met / len(self.timing_windows)


# ── Ledger builder ───────────────────────────────────────────────────────────

def build_ledger(
    case,
    turns: list,
    trace,
    pharm_events: list[dict] | None = None,
) -> EvidenceLedger:
    """
    Build an EvidenceLedger from session artifacts.

    Args:
        case: CaseBlueprint
        turns: list[Turn] from scorer
        trace: PhysioTrace
        pharm_events: PharmacologyEngine.events (if available)
    """
    from .scorer import Turn
    from shared.schemas.case import CaseBlueprint
    from .rubric import NIRIKSHAK_VERSION
    from ..physio.state import PhysioTrace as PT

    ledger = EvidenceLedger(evaluator_version=f"nirikshak-{NIRIKSHAK_VERSION}")
    pharm_events = pharm_events or (trace.events if trace else [])

    # Session metadata
    if trace and trace.snapshots:
        ledger.session_duration_s = trace.snapshots[-1].t
        ledger.patient_died = any(s.dead for s in trace.snapshots)

    # Timing windows for critical actions
    RED_FLAG_WINDOWS = {
        "MI": {"order_ecg_within_10min": (300, 600), "give_aspirin_325_chewed": (300, 600)},
        "sepsis": {"measure_vitals": (120, 300), "give_iv_fluid_ns": (300, 900)},
        "stroke": {"order_ct_head": (300, 900)},
    }
    red_flag = case.resolved_red_flag() if hasattr(case, "resolved_red_flag") else None
    windows = RED_FLAG_WINDOWS.get(red_flag, {})

    action_turns = {t.action: t.ts for t in turns if t.action and isinstance(t, Turn)}
    action_times = {i["action"]: i["t"] for i in (case.expected_actions if False else [])
                    } if False else {}
    # Build from interventions embedded in turns
    for t in turns:
        if isinstance(t, Turn) and t.action:
            action_times[t.action] = t.ts

    for action, (ideal, acceptable) in windows.items():
        # Map action names to turn actions
        tw = TimingWindow(
            action=action,
            ideal_before_s=ideal,
            acceptable_before_s=acceptable,
            actual_s=action_times.get(action),
        )
        ledger.timing_windows.append(tw)

    # Drug administration evidence from pharm events
    for evt in pharm_events:
        kind = evt.get("kind", "")
        if kind == "dose":
            ledger.drug_admins.append(DrugAdminEvidence(
                drug=evt["drug"],
                dose_mg=evt.get("amount_mg", 0),
                route=evt.get("route", ""),
                t_admin_s=evt.get("t", evt.get("t_min", 0) * 60),
            ))
            ledger.add(
                t=evt.get("t", evt.get("t_min", 0) * 60),
                category=EvidenceCategory.PHARMACOLOGICAL,
                tag="drug_administered",
                payload={"drug": evt["drug"], "dose": evt.get("amount_mg", 0)},
            )
        elif kind == "medication":
            ledger.drug_admins.append(DrugAdminEvidence(
                drug=evt["drug"],
                dose_mg=evt.get("dose", 0),
                route=evt.get("route", ""),
                t_admin_s=evt.get("t", 0),
            ))
            ledger.add(
                t=evt.get("t", 0),
                category=EvidenceCategory.PHARMACOLOGICAL,
                tag="drug_administered",
                payload={"drug": evt["drug"], "dose": evt.get("dose", 0)},
            )
        elif kind == "allergy":
            # Find and mark the drug admin
            for da in ledger.drug_admins:
                if da.drug == evt.get("drug"):
                    da.was_contraindicated = True
            ledger.add(
                t=evt.get("t", 0), category=EvidenceCategory.SAFETY,
                tag="allergy_triggered", payload={"drug": evt.get("drug")},
                score_impact=-15.0,
            )
        elif kind == "contraindicated":
            for da in ledger.drug_admins:
                if da.drug == evt.get("drug"):
                    da.was_contraindicated = True
            ledger.add(
                t=evt.get("t", 0), category=EvidenceCategory.SAFETY,
                tag="contraindication_ignored", payload={"drug": evt.get("drug")},
                score_impact=-10.0,
            )
        elif kind == "toxic_level":
            for da in ledger.drug_admins:
                if da.drug == evt.get("drug"):
                    da.was_toxic = True
            ledger.add(
                t=evt.get("t", 0), category=EvidenceCategory.SAFETY,
                tag="toxic_plasma_level", payload={"drug": evt.get("drug"), "cp": evt.get("cp")},
                score_impact=-12.0,
            )
        elif kind == "reversal":
            for rn in evt.get("reversed", []):
                for da in ledger.drug_admins:
                    if da.drug == rn:
                        da.was_reversed = True
                        da.reversal_drug = evt.get("antidote")
            ledger.add(
                t=evt.get("t", 0), category=EvidenceCategory.PHARMACOLOGICAL,
                tag="reversal_given",
                payload={"antidote": evt.get("antidote"), "reversed": evt.get("reversed")},
                score_impact=+5.0,
            )

    # Crisis evidence from trace
    crisis_window = trace.first_crisis_window() if trace else None
    if crisis_window:
        t0, t1 = crisis_window
        ce = CrisisEvidence(t_start_s=t0, t_end_s=t1)
        # Check if learner responded during crisis
        for t in turns:
            if isinstance(t, Turn) and t.speaker == "learner" and t0 <= t.ts <= (t1 or t0) + 30:
                ce.learner_responded = True
                ce.response_latency_s = t.ts - t0
                break
        ledger.crises.append(ce)

    # Communication evidence
    for t in turns:
        if not isinstance(t, Turn):
            continue
        if t.speaker == "learner":
            ledger.add(t.ts, EvidenceCategory.COMMUNICATION, "learner_utterance",
                       {"text_len": len(t.text), "lang": t.lang})

    return ledger
