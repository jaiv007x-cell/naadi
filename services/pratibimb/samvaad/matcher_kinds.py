"""SAMVAAD matcher kinds — extend Nirikshak registry in SAMVAAD.c when detectors land.

SAMVAAD.b keeps kinds here so clinical Nirikshak stays closed under Framework freeze.
Merge path: union into matcher_registry.KNOWN_MATCHER_KINDS + stub/real _MATCHERS.
"""
from __future__ import annotations

from typing import Final

SAMVAAD_MATCHER_KINDS: Final[frozenset[str]] = frozenset(
    {
        "action_sequence",
        "action_present",
        "register_appropriateness",
        "sbar_fields_present",
        "visual_frame_check",
        "debrief_self_report_structured",
    }
)
