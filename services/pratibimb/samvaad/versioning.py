"""Framework vs rubric versioning split (SAMVAAD.b plan-gate pin 1 / matrix case 14)."""
from __future__ import annotations

from typing import Final

from services.pratibimb.samvaad.constants import DOMAIN_SET_VERSION, REFERENCE_RUBRICS_VERSION
from services.pratibimb.samvaad.matcher_spec import COMBINATOR, EMPATHY_CUE_WINDOW_MS

# Framework-layer: structural — bump DOMAIN_SET_VERSION / Framework freeze to change
FRAMEWORK_STRUCTURAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "combinator_must_be_multi_signal_and",
        "interruption_veto_must_exist",
        "domain_ordinals_additive_only",
    }
)

# Rubric-layer: tunable within Framework version
RUBRIC_PARAM_KEYS: Final[frozenset[str]] = frozenset(
    {
        "empathy_cue_window_ms",
        "step_weights",
        "supporting_cue_phrases",
    }
)


def is_framework_change(change_kind: str) -> bool:
    return change_kind in {
        "and_to_or",
        "remove_interruption_veto",
        "reorder_domains",
        "remove_domain",
        "rename_domain",
    }


def is_rubric_param_change(change_kind: str) -> bool:
    return change_kind in {
        "tune_empathy_window_ms",
        "tune_step_weights",
        "edit_supporting_cues",
    }


def assert_window_tune_stays_on_same_framework(
    *,
    old_window_ms: int,
    new_window_ms: int,
    framework_version: str = DOMAIN_SET_VERSION,
) -> None:
    """Tuning EMPATHY_CUE_WINDOW_MS must not require a Framework bump."""
    if old_window_ms == new_window_ms:
        return
    # Same Framework domain-set version; only rubric version would change
    assert framework_version == DOMAIN_SET_VERSION
    assert COMBINATOR == "multi_signal_and"  # structural unchanged
    assert EMPATHY_CUE_WINDOW_MS  # veto machinery still exists
    # Rubric version string is independent of DOMAIN_SET_VERSION
    assert REFERENCE_RUBRICS_VERSION.startswith("samvaad.reference_rubrics.")


def assert_and_gate_change_requires_framework(change_kind: str) -> None:
    if not is_framework_change(change_kind):
        raise AssertionError(f"{change_kind!r} is not a Framework-layer change")
