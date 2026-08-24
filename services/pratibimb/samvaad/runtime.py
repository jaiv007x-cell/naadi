"""SAMVAAD.b — empathy AND-combinator runtime + digital action_sequence stubs.

Matchers extend Nirikshak DSL conceptually; evaluation lives here until
full registry wiring in a later slice. Framework: AND + veto *exists*.
Rubric: window_ms is a tunable param (default EMPATHY_CUE_WINDOW_MS).
"""
from __future__ import annotations

from typing import Any

from services.pratibimb.samvaad.matcher_spec import (
    COMBINATOR,
    EMPATHY_CUE_WINDOW_MS,
    INTERRUPTION_VETO,
    REQUIRED_STEPS,
    empathy_hit_fires,
)


def evaluate_empathy_and(
    step_results: dict[str, bool],
    *,
    interruptions_after_ack: int,
    window_ms: int | None = None,
    interruption_timestamps_ms: list[int] | None = None,
    cue_onset_ms: int = 0,
) -> tuple[bool, dict[str, Any]]:
    """
    Runtime stub for I-S-6.

    ``window_ms`` is a *rubric-level* parameter (defaults to matcher-spec constant).
    Changing it does not require a Framework bump.
    """
    window = EMPATHY_CUE_WINDOW_MS if window_ms is None else window_ms
    # Interruptions outside the cue window do not count toward veto
    if interruption_timestamps_ms is not None:
        in_window = [
            t
            for t in interruption_timestamps_ms
            if cue_onset_ms <= t <= cue_onset_ms + window
        ]
        interruptions_after_ack = len(in_window)

    fired = empathy_hit_fires(
        step_results, interruptions_after_ack=interruptions_after_ack
    )
    evidence = {
        "combinator": COMBINATOR,
        "required_steps": list(REQUIRED_STEPS),
        "step_results": dict(step_results),
        "window_ms": window,
        "interruptions_after_ack": interruptions_after_ack,
        "max_interruptions": INTERRUPTION_VETO["max_interruptions_after_acknowledge"],
        "matched": fired,
    }
    return fired, evidence


def evaluate_action_sequence(
    observed_steps: list[str],
    required_steps: list[str],
) -> tuple[bool, dict[str, Any]]:
    """Digital literacy / generic action_sequence — order-preserving subsequence."""
    it = iter(observed_steps)
    matched = all(step in it for step in required_steps)
    return matched, {
        "kind": "action_sequence",
        "required": list(required_steps),
        "observed": list(observed_steps),
        "matched": matched,
    }


def rubric_window_parity(rubric: dict[str, Any], *, default_ms: int = EMPATHY_CUE_WINDOW_MS) -> bool:
    """True when empathy rubric param matches matcher-spec constant (two-place pin)."""
    if rubric.get("competency") != "comm.empathy.acknowledge_distress":
        return True
    params = rubric.get("params") or {}
    return params.get("empathy_cue_window_ms") == default_ms
