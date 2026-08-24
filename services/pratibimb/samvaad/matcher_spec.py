"""Empathy interaction-behavior matcher spec (I-S-6).

Multi-signal AND combinator — not single-cue OR / phrase-match.
Runtime detectors land later; this module is the DSL contract.
"""
from __future__ import annotations

from typing import Final

MATCHER_KIND: Final[str] = "action_sequence"

COMBINATOR: Final[str] = "multi_signal_and"

REQUIRED_STEPS: Final[tuple[str, ...]] = (
    "pause_after_distress_cue",
    "acknowledge_concern",
    "jargon_reduction_or_plain_restatement",
    "teach_back_or_check_understanding",
)

STEP_WEIGHTS: Final[dict[str, float]] = {
    "pause_after_distress_cue": 0.25,
    "acknowledge_concern": 0.25,
    "jargon_reduction_or_plain_restatement": 0.25,
    "teach_back_or_check_understanding": 0.25,
}

DEFAULT_MAX_RESPONSE_LATENCY_MS: Final[int] = 2500

# I-S-6 — rubric-level parameter (tunable without Framework bump).
# Provisional pending ops baseline / Phase 1 empirical tune — not Framework-frozen.
EMPATHY_CUE_WINDOW_MS: Final[int] = 5000

INTERRUPTION_VETO: Final[dict[str, int]] = {
    "max_interruptions_after_acknowledge": 1,
    "window_ms_from_empathy_cue_onset": EMPATHY_CUE_WINDOW_MS,
}

SUPPORTING_CUE_PARAM: Final[str] = "supporting_cue_phrases"

FORBIDDEN_CREDENTIAL_SIGNALS: Final[frozenset[str]] = frozenset(
    {
        "voice_tone_warmth",
        "prosody_score",
        "sentiment_score",
        "emotion_from_audio",
    }
)

MATCHER_SPEC_VERSION: Final[str] = "samvaad.empathy_matcher.v1"


def empathy_matcher_params(
    *,
    max_response_latency_ms: int = DEFAULT_MAX_RESPONSE_LATENCY_MS,
    supporting_cue_phrases: list[str] | None = None,
) -> dict:
    return {
        "combinator": COMBINATOR,
        "required": list(REQUIRED_STEPS),
        "step_weights": dict(STEP_WEIGHTS),
        "max_response_latency_ms_after_distress_cue": max_response_latency_ms,
        "empathy_cue_window_ms": EMPATHY_CUE_WINDOW_MS,
        "interruption_veto": dict(INTERRUPTION_VETO),
        SUPPORTING_CUE_PARAM: supporting_cue_phrases or [],
        "matcher_spec_version": MATCHER_SPEC_VERSION,
    }


def empathy_hit_fires(step_results: dict[str, bool], *, interruptions_after_ack: int) -> bool:
    """AND combinator + interruption veto — reference for runtime / tests."""
    if interruptions_after_ack > INTERRUPTION_VETO["max_interruptions_after_acknowledge"]:
        return False
    return all(step_results.get(s, False) for s in REQUIRED_STEPS)
