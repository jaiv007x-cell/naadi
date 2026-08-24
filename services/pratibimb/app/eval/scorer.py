from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from shared.schemas.case import CaseBlueprint
from shared.schemas.migration import MigrationReport
from shared.schemas.scoring import CompetencyUpdate, RubricScore
from shared.schemas.trace import (
    DiagnosisEvent,
    EscalationEvent,
    HandoffEvent,
    OrderEvent,
)

from ..physio.state import PhysioTrace
from .judge import LLMJudge
from .ledger import EvidenceLedger, EvidenceCategory, build_ledger
from .nirikshak import CaseGrade, Nirikshak
from .rubric import Axis, Competency, GradingBlueprint, RUBRIC

log = logging.getLogger("pratibimb.scorer")

RED_FLAGS = {
    "sepsis": ["lactate>2", "MAP<65", "qSOFA>=2"],
    "MI": ["ST-elevation", "troponin+", "chest-pain+diaphoresis"],
    "stroke": ["FAST+", "onset<4.5h"],
}


@dataclass
class Turn:
    speaker: str
    text: str
    ts: float
    action: str | None = None
    lang: str = "hi"


@dataclass
class ScoreCard:
    competency_scores: dict[str, float]
    total: float
    flags: list[str]
    rationale: dict[str, str]
    ledger: EvidenceLedger | None = None
    case_grade: CaseGrade | None = None
    trace_migration: MigrationReport | None = None


_SBAR_FIELDS = ("situation", "background", "assessment", "recommendation")


def project_turns(trace, turns: list[Turn]) -> None:
    """
    Project learner turns onto a typed `shared.schemas.trace.PhysioTrace`.

    Turn actions carry a `kind:value` shape (`order:ECG`, `dx:anaphylaxis`,
    `handoff:physician`, `escalate:code_blue`); anything else is dialogue and
    contributes no gradable event.
    """
    for t in turns:
        if not t.action or ":" not in t.action:
            continue
        kind, _, value = t.action.partition(":")
        if kind == "order":
            trace.record_order(OrderEvent(order_id=value, t_s=t.ts))
        elif kind == "dx":
            trace.record_diagnosis(
                DiagnosisEvent(dx=value or t.text, confidence=1.0, t_s=t.ts)
            )
        elif kind == "handoff":
            text = t.text.lower()
            fields = frozenset(f for f in _SBAR_FIELDS if f in text)
            trace.record_handoff(HandoffEvent(fields=fields, target=value, t_s=t.ts))
        elif kind == "escalate":
            trace.record_escalation(EscalationEvent(target=value, t_s=t.ts))


def grade_case(
    blueprint: GradingBlueprint,
    physio: PhysioTrace,
    turns: list[Turn],
    case_id: str = "",
) -> tuple[CaseGrade, MigrationReport]:
    """
    Run Nirikshak over the typed projection of a session.

    Returns the grade together with the trace migration audit. The audit is a
    return value rather than a log line because it records which session
    evidence was discarded on the way in.
    """
    trace, report = physio.to_typed_trace(
        case_id=case_id or blueprint.case_id,
        case_version=blueprint.case_version,
    )
    project_turns(trace, turns)
    report.consume("learner_turns", sum(1 for t in turns if t.action and ":" in t.action))
    if not report.is_lossless:
        log.warning("trace migration for case %s - %s", blueprint.case_id, report.audit_line())
    return Nirikshak(blueprint, trace).grade(), report


_AXIS_TO_RUBRIC_DIM: dict[str, tuple[tuple[Axis, float], ...]] = {
    "clinical_reasoning": ((Axis.DIAGNOSTIC, 0.6), (Axis.ACTION, 0.4)),
    "procedural_correctness": ((Axis.ACTION, 0.5), (Axis.TIMING, 0.5)),
    "communication": ((Axis.COMMUNICATION, 1.0),),
    "ethical_legal": ((Axis.SAFETY, 1.0),),
    "stress_modulated": ((Axis.TIMING, 0.7), (Axis.SAFETY, 0.3)),
}


def grade_to_rubric(grade: CaseGrade) -> RubricScore:
    """Map a case-level CaseGrade onto the five-dimension session rubric."""
    norms = {a.axis: a.normalized for a in grade.axis_scores}

    def dim(name: str) -> float:
        blend = sum(norms.get(axis, 0.0) * w for axis, w in _AXIS_TO_RUBRIC_DIM[name])
        return round(blend * 10, 2)

    notes = [
        f"nirikshak: {grade.letter} ({grade.overall:.2f})",
        f"manifest: {grade.evaluation_manifest_hash[:12]}",
    ]
    for hit_id in grade.critical_violations:
        notes.append(f"critical violation: {hit_id}")
    for outcome in grade.outcomes:
        if not outcome.matched:
            notes.append(f"missed [{outcome.axis.value}] {outcome.hit_id}")

    return RubricScore(
        clinical_reasoning=dim("clinical_reasoning"),
        procedural_correctness=dim("procedural_correctness"),
        communication=dim("communication"),
        ethical_legal=dim("ethical_legal"),
        stress_modulated=dim("stress_modulated"),
        notes=notes,
    )


def _keyword_hits(text: str, keys: list[str]) -> int:
    t = text.lower()
    return sum(1 for k in keys if re.search(rf"\b{re.escape(k.lower())}\b", t))


def _clinical_judgment(case: CaseBlueprint, turns: list[Turn], ledger: EvidenceLedger) -> tuple[float, str]:
    needed = case.expected_orders
    action_to_order = {
        "order:ECG": "ECG",
        "order:troponin": "troponin",
        "give:aspirin_325": "aspirin_325",
        "give:atorvastatin_80": "atorvastatin_80",
        "transfer:pci": "pci_transfer",
    }
    ordered = {action_to_order[t.action] for t in turns if t.action in action_to_order}
    hit = len(needed & ordered) / max(1, len(needed))

    # Timing window bonus/penalty from ledger
    timing_ratio = ledger.timing_met_ratio()
    time_penalty = 0.0
    red_flag = case.resolved_red_flag()
    if red_flag and red_flag in RED_FLAGS:
        first = next(
            (t.ts for t in turns if t.action and t.action.split(":")[-1] in needed),
            None,
        )
        if first is None or first > 8 * 60:
            time_penalty = 0.25

    # Safety penalty from adverse drug events
    safety_penalty = min(0.3, len(ledger.adverse_events()) * 0.1)

    # Reversal credit
    reversal_credit = 0.05 * len([d for d in ledger.drug_admins if d.was_reversed])

    raw = max(0.0, hit * timing_ratio - time_penalty - safety_penalty + reversal_credit)
    score = raw * 100

    rationale_parts = [
        f"orders={hit:.0%}",
        f"timing={timing_ratio:.0%}",
        f"time_pen={time_penalty}",
        f"safety_pen={safety_penalty}",
        f"reversal_credit={reversal_credit}",
    ]
    return score, " ".join(rationale_parts)


def _pharmacological(ledger: EvidenceLedger) -> tuple[float, str]:
    """Score pharmacological decision-making from the evidence ledger."""
    if not ledger.drug_admins:
        return 70.0, "no drugs administered"

    total_drugs = len(ledger.drug_admins)
    adverse = len(ledger.adverse_events())
    correct = total_drugs - adverse

    safety_impact = ledger.total_impact(EvidenceCategory.SAFETY)
    pharm_impact = ledger.total_impact(EvidenceCategory.PHARMACOLOGICAL)

    base = (correct / max(1, total_drugs)) * 80
    adjusted = max(0.0, min(100.0, base + safety_impact + pharm_impact))

    parts = [
        f"drugs={total_drugs}",
        f"adverse={adverse}",
        f"safety_impact={safety_impact:.1f}",
        f"pharm_impact={pharm_impact:.1f}",
    ]
    return adjusted, " ".join(parts)


def _communication(case: CaseBlueprint, turns: list[Turn]) -> tuple[float, str]:
    learner = [t for t in turns if t.speaker == "learner"]
    if not learner:
        return 0.0, "no learner turns"
    open_qs = sum(
        1 for t in learner if t.text.strip().endswith("?") and len(t.text.split()) > 6
    )
    empathy = sum(
        _keyword_hits(
            t.text,
            ["समझ", "चिंता", "आराम", "don't worry", "I understand", "ठीक होगा", "namaste", "kasa"],
        )
        for t in learner
    )
    jargon = sum(
        _keyword_hits(t.text, ["myocardial", "infarction", "tachypnea", "hemodynamic"])
        for t in learner
    )
    lang_match = sum(1 for t in learner if t.lang == case.patient_lang) / len(learner)
    raw = (
        0.35 * min(open_qs / 5, 1)
        + 0.30 * min(empathy / 4, 1)
        + 0.15 * (1 - min(jargon / 3, 1))
        + 0.20 * lang_match
    )
    return raw * 100, f"open_q={open_qs}, empathy={empathy}, jargon={jargon}, lang_match={lang_match:.0%}"


def _stress_response(physio: PhysioTrace, turns: list[Turn], ledger: EvidenceLedger) -> tuple[float, str]:
    if not ledger.crises:
        crisis = physio.first_crisis_window()
        if not crisis:
            return 85.0, "no crisis window; default"
        t0, t1 = crisis
    else:
        ce = ledger.crises[0]
        t0, t1 = ce.t_start_s, ce.t_end_s or ce.t_start_s

    reaction = next(
        (t for t in turns if t.speaker == "learner" and t0 <= t.ts <= t1 + 30),
        None,
    )
    if reaction is None:
        return 30.0, "no learner reaction inside crisis window"
    latency = reaction.ts - t0
    calm_words = _keyword_hits(reaction.text, ["breathe", "step", "let's", "check", "monitor", "ठीक"])
    panic_words = _keyword_hits(reaction.text, ["oh no", "shit", "हे भगवान", "help help"])
    score = max(0.0, 100 - latency * 2 - panic_words * 15 + calm_words * 3)
    return min(score, 100.0), f"latency={latency:.1f}s calm={calm_words} panic={panic_words}"


def _procedural(case: CaseBlueprint, turns: list[Turn]) -> tuple[float, str]:
    seq = [t.action for t in turns if t.action]
    expected_seq = case.expected_procedure_sequence or []
    if not expected_seq:
        return 80.0, "no procedure required"
    m, n = len(seq), len(expected_seq)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m):
        for j in range(n):
            dp[i + 1][j + 1] = (
                dp[i][j] + 1 if seq[i] == expected_seq[j] else max(dp[i + 1][j], dp[i][j + 1])
            )
    lcs = dp[m][n] / n
    return lcs * 100, f"lcs={lcs:.0%}"


def _hallucination_flags(case: CaseBlueprint, turns: list[Turn]) -> list[str]:
    allowed = case.allowed_symptoms
    vocab = case.resolved_symptom_vocab()
    flags = []
    for t in turns:
        if t.speaker != "patient":
            continue
        for tok in re.findall(r"[a-zA-Z\u0900-\u097F]{4,}", t.text.lower()):
            if tok in vocab and tok not in allowed:
                flags.append(f"patient invented symptom: {tok} @ {t.ts:.1f}s")
    return flags


def score_session(
    case: CaseBlueprint,
    turns: list[Turn],
    physio: PhysioTrace,
    pharm_events: list[dict] | None = None,
    blueprint: GradingBlueprint | None = None,
) -> ScoreCard:
    ledger = build_ledger(case, turns, physio, pharm_events)
    case_grade: CaseGrade | None = None
    trace_migration: MigrationReport | None = None
    if blueprint is not None:
        case_grade, trace_migration = grade_case(
            blueprint, physio, turns, case_id=case.case_id
        )

    cj, cj_r = _clinical_judgment(case, turns, ledger)
    ph, ph_r = _pharmacological(ledger)
    co, co_r = _communication(case, turns)
    sr, sr_r = _stress_response(physio, turns, ledger)
    pr, pr_r = _procedural(case, turns)

    scores = {
        Competency.CLINICAL_JUDGMENT.value: cj,
        Competency.PHARMACOLOGICAL.value: ph,
        Competency.COMMUNICATION.value: co,
        Competency.STRESS_RESPONSE.value: sr,
        Competency.PROCEDURAL.value: pr,
    }
    weights = {k: RUBRIC[k].weight for k in scores}
    total = sum(scores[k] * weights[k] for k in scores) / sum(weights.values())
    return ScoreCard(
        competency_scores=scores,
        total=round(total, 2),
        flags=_hallucination_flags(case, turns),
        rationale={
            Competency.CLINICAL_JUDGMENT.value: cj_r,
            Competency.PHARMACOLOGICAL.value: ph_r,
            Competency.COMMUNICATION.value: co_r,
            Competency.STRESS_RESPONSE.value: sr_r,
            Competency.PROCEDURAL.value: pr_r,
        },
        ledger=ledger,
        case_grade=case_grade,
        trace_migration=trace_migration,
    )


def _action_to_turn_action(action: str) -> str | None:
    mapping = {
        "order_ecg_within_10min": "order:ECG",
        "order_ecg": "order:ECG",
        "order_troponin": "order:troponin",
        "give_aspirin_325_chewed": "give:aspirin_325",
        "give_atorvastatin_80": "give:atorvastatin_80",
        "arrange_pci_transfer": "transfer:pci",
        "measure_vitals": "vitals",
    }
    return mapping.get(action)


def build_turns(
    action_log: list[dict],
    transcript: list[dict],
    patient_lang: str,
) -> list[Turn]:
    turns: list[Turn] = []
    for t in transcript:
        role = t.get("role", "")
        speaker = "learner" if role == "student" else role
        turns.append(
            Turn(
                speaker=speaker,
                text=t.get("content", ""),
                ts=t.get("ts", 0.0),
                lang=t.get("lang", patient_lang),
            )
        )
    for a in action_log:
        act = _action_to_turn_action(a["action"])
        if act:
            turns.append(Turn(speaker="system", text=a["action"], ts=a["t"], action=act))
    turns.sort(key=lambda x: x.ts)
    return turns


def scorecard_to_rubric(card: ScoreCard) -> RubricScore:
    notes = list(card.flags)
    notes.extend(f"{k}: {v}" for k, v in card.rationale.items())
    ethical = 8.0 if not card.flags else max(3.0, 8.0 - len(card.flags) * 1.5)

    # Blend pharmacological into clinical_reasoning for the 5-dim rubric output
    pharm_score = card.competency_scores.get(Competency.PHARMACOLOGICAL.value, 70.0)
    clinical_raw = card.competency_scores.get(Competency.CLINICAL_JUDGMENT.value, 0)
    blended_clinical = (clinical_raw * 0.6 + pharm_score * 0.4)

    return RubricScore(
        clinical_reasoning=round(blended_clinical / 10, 2),
        procedural_correctness=round(card.competency_scores.get(Competency.PROCEDURAL.value, 0) / 10, 2),
        communication=round(card.competency_scores.get(Competency.COMMUNICATION.value, 0) / 10, 2),
        ethical_legal=round(ethical, 2),
        stress_modulated=round(card.competency_scores.get(Competency.STRESS_RESPONSE.value, 0) / 10, 2),
        notes=notes,
    )


class Scorer:
    """
    Hybrid scorer.

    Without a `GradingBlueprint` it runs the deterministic competency scorer.
    With one it defers the objective dimensions to Nirikshak and keeps the LLM
    judge for the dimensions a rubric cannot observe (tone, ethics).
    """

    def __init__(self, judge: LLMJudge | None = None):
        self.judge = judge or LLMJudge()

    async def score(
        self,
        case: CaseBlueprint,
        action_log: list[dict],
        transcript: list[dict],
        stress_delta: float,
        physio: PhysioTrace | None = None,
        blueprint: GradingBlueprint | None = None,
    ) -> RubricScore:
        turns = build_turns(action_log, transcript, case.patient_lang)
        trace = physio or PhysioTrace()
        card = score_session(case, turns, trace, blueprint=blueprint)
        if card.case_grade is not None:
            return await self._score_with_grade(case, transcript, stress_delta, card.case_grade)

        try:
            llm_comm, llm_ethical, llm_notes = await self.judge.score_communication(case, transcript)
            rubric = scorecard_to_rubric(card)
            rubric.communication = round((rubric.communication + llm_comm) / 2, 2)
            rubric.ethical_legal = round((rubric.ethical_legal + llm_ethical) / 2, 2)
            rubric.notes.extend(llm_notes)
            if stress_delta < 0:
                rubric.stress_modulated = max(0.0, rubric.stress_modulated + stress_delta)
            return rubric
        except Exception:
            rubric = scorecard_to_rubric(card)
            if stress_delta < 0:
                rubric.stress_modulated = max(0.0, rubric.stress_modulated + stress_delta)
            return rubric

    async def _score_with_grade(
        self,
        case: CaseBlueprint,
        transcript: list[dict],
        stress_delta: float,
        grade: CaseGrade,
    ) -> RubricScore:
        rubric = grade_to_rubric(grade)
        try:
            llm_comm, llm_ethical, llm_notes = await self.judge.score_communication(case, transcript)
            rubric.communication = round((rubric.communication + llm_comm) / 2, 2)
            rubric.ethical_legal = round((rubric.ethical_legal + llm_ethical) / 2, 2)
            rubric.notes.extend(llm_notes)
        except Exception:
            pass
        if stress_delta < 0:
            rubric.stress_modulated = max(0.0, rubric.stress_modulated + stress_delta)
        return rubric

    def competency_updates(
        self, case: CaseBlueprint, rubric: RubricScore, actions_taken: list[str]
    ) -> list[CompetencyUpdate]:
        updates = []
        for tag in case.competency_tags:
            delta = (rubric.overall - 5.0) / 10.0
            if "ecg" in tag and ("order_ecg" in actions_taken or "order_ecg_within_10min" in actions_taken):
                delta += 0.1
            updates.append(
                CompetencyUpdate(
                    learner_id="",
                    competency_tag=tag,
                    delta=round(delta, 3),
                    evidence=f"Session score {rubric.overall}",
                )
            )
        return updates
