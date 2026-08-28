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

ERROR_KIND_IMPLICIT_CUE_FORBIDDEN = "implicit_cue_forbidden"
ERROR_KIND_INVALID_MATCHER_CONFIG = "invalid_matcher_config"


class SamvaadMatcherError(ValueError):
    """Matcher failure with a stable, searchable error kind."""

    def __init__(self, message: str, *, error_kind: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind


def validate_matcher_params(params: dict[str, Any]) -> None:
    """Validate rubric parameters when the rubric/config is parsed."""
    key = (
        "min_occurrences_per_turn"
        if "min_occurrences_per_turn" in params
        else "min_occurrences"
    )
    if key in params and int(params[key]) < 1:
        raise SamvaadMatcherError(
            f"{key} must be >= 1",
            error_kind=ERROR_KIND_INVALID_MATCHER_CONFIG,
        )


def evaluate_empathy_and(
    step_results: dict[str, bool],
    *,
    interruptions_after_ack: int,
    window_ms: int | None = None,
    interruption_timestamps_ms: list[int] | None = None,
    cue_onset_ms: int = 0,
    step_timestamps_ms: dict[str, int] | None = None,
    cue_explicit: bool = True,
    allow_implicit_cue: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """
    Runtime stub for I-S-6.

    ``window_ms`` is a *rubric-level* parameter (defaults to matcher-spec constant).
    Changing it does not require a Framework bump.
    """
    if not cue_explicit and not allow_implicit_cue:
        raise SamvaadMatcherError(
            "implicit empathy cue forbidden; explicit distress cue required",
            error_kind=ERROR_KIND_IMPLICIT_CUE_FORBIDDEN,
        )

    window = EMPATHY_CUE_WINDOW_MS if window_ms is None else window_ms
    effective_steps = dict(step_results)
    if step_timestamps_ms is not None:
        for step, matched in effective_steps.items():
            timestamp = step_timestamps_ms.get(step)
            if matched and (
                timestamp is None
                or not cue_onset_ms <= timestamp <= cue_onset_ms + window
            ):
                effective_steps[step] = False

    # Interruptions outside the cue window do not count toward veto
    if interruption_timestamps_ms is not None:
        in_window = [
            t
            for t in interruption_timestamps_ms
            if cue_onset_ms <= t <= cue_onset_ms + window
        ]
        interruptions_after_ack = len(in_window)

    fired = empathy_hit_fires(
        effective_steps, interruptions_after_ack=interruptions_after_ack
    )
    evidence = {
        "combinator": COMBINATOR,
        "required_steps": list(REQUIRED_STEPS),
        "step_results": effective_steps,
        "step_timestamps_ms": dict(step_timestamps_ms or {}),
        "window_ms": window,
        "allow_implicit_cue": allow_implicit_cue,
        "cue_explicit": cue_explicit,
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
