"""SAMVAAD.c — Nirikshak matcher implementations delegating to samvaad.runtime."""
from __future__ import annotations

from typing import Any, Protocol

from services.pratibimb.samvaad.matcher_spec import COMBINATOR, REQUIRED_STEPS
from services.pratibimb.samvaad.runtime import evaluate_action_sequence, evaluate_empathy_and


class SamvaadEvalContext(Protocol):
    samvaad_context: dict[str, Any]


def _ctx(evaluator: SamvaadEvalContext) -> dict[str, Any]:
    return getattr(evaluator, "samvaad_context", {}) or {}


def m_action_sequence(evaluator: SamvaadEvalContext, hit: Any) -> tuple[bool, dict]:
    params = hit.params or {}
    matcher = getattr(hit, "matcher", None) or {}
    if isinstance(matcher, dict):
        required = matcher.get("required") or params.get("required") or []
    else:
        required = params.get("required") or []

    if params.get("combinator") == COMBINATOR:
        step_results = _ctx(evaluator).get("step_results") or {
            s: s in (_ctx(evaluator).get("observed_steps") or []) for s in REQUIRED_STEPS
        }
        interruptions = int(_ctx(evaluator).get("interruptions_after_ack", 0))
        return evaluate_empathy_and(
            step_results,
            interruptions_after_ack=interruptions,
            window_ms=params.get("empathy_cue_window_ms"),
            interruption_timestamps_ms=_ctx(evaluator).get("interruption_timestamps_ms"),
            cue_onset_ms=int(_ctx(evaluator).get("cue_onset_ms", 0)),
        )

    observed = list(_ctx(evaluator).get("observed_steps") or [])
    return evaluate_action_sequence(observed, list(required))


def m_action_present(evaluator: SamvaadEvalContext, hit: Any) -> tuple[bool, dict]:
    matcher = getattr(hit, "matcher", None) or {}
    action = (
        (matcher.get("action") if isinstance(matcher, dict) else None)
        or hit.params.get("action")
    )
    observed = list(_ctx(evaluator).get("observed_steps") or _ctx(evaluator).get("actions") or [])
    matched = action in observed
    return matched, {"kind": "action_present", "action": action, "observed": observed}


def m_register_appropriateness(evaluator: SamvaadEvalContext, hit: Any) -> tuple[bool, dict]:
    flags = _ctx(evaluator).get("register_flags") or {}
    params = hit.params or {}
    ok = bool(flags.get("vernacular_substitution")) and not flags.get(
        "language_detection_only"
    )
    if params.get("disallow_unexplained_jargon"):
        ok = ok and not flags.get("unexplained_jargon")
    return ok, {"kind": "register_appropriateness", "flags": flags}


def m_sbar_fields_present(evaluator: SamvaadEvalContext, hit: Any) -> tuple[bool, dict]:
    matcher = getattr(hit, "matcher", None) or {}
    required = matcher.get("required_elements") if isinstance(matcher, dict) else []
    present = set(_ctx(evaluator).get("sbar_fields") or [])
    missing = [e for e in required if e not in present]
    return not missing, {"required": list(required), "present": sorted(present), "missing": missing}


def m_visual_frame_check(evaluator: SamvaadEvalContext, hit: Any) -> tuple[bool, dict]:
    matcher = getattr(hit, "matcher", None) or {}
    checks = matcher.get("checks") if isinstance(matcher, dict) else []
    results = _ctx(evaluator).get("visual_checks") or {}
    failed = [c for c in checks if not results.get(c)]
    return not failed, {"checks": list(checks), "results": dict(results), "failed": failed}


def m_debrief_self_report_structured(evaluator: SamvaadEvalContext, hit: Any) -> tuple[bool, dict]:
    params = hit.params or {}
    utterances = list(_ctx(evaluator).get("debrief_utterances") or [])
    techniques = params.get("techniques_referenced") or []
    reject = set(params.get("reject_vague") or [])
    min_occ = int(params.get("min_occurrences", 1))
    hits = [
        u
        for u in utterances
        if u not in reject and any(t in u.lower().replace(" ", "_") for t in techniques)
    ]
    matched = len(hits) >= min_occ
    return matched, {"techniques": techniques, "hits": hits}


SAMVAAD_MATCHERS: dict[str, Any] = {
    "action_sequence": m_action_sequence,
    "action_present": m_action_present,
    "register_appropriateness": m_register_appropriateness,
    "sbar_fields_present": m_sbar_fields_present,
    "visual_frame_check": m_visual_frame_check,
    "debrief_self_report_structured": m_debrief_self_report_structured,
}
