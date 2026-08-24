"""Pinned constants for SAMVAAD Framework v1 / SAMVAAD.a."""
from __future__ import annotations

from typing import Final

PHASE1_PILOT_IMMINENT: Final[bool] = False

REFERENCE_RUBRICS_VERSION: Final[str] = "samvaad.reference_rubrics.v1.1"

DOMAIN_SET_VERSION: Final[str] = "samvaad.domains.v1"

# I-S-1 — ordinal-stable additive-only set.
# v1 ordinals A–I are fixed; v2 may append J, K, … never reorder/rename/remove A–I.
# Sealed transcripts under I-S-14 remain interpretable: domain C in v1 == domain C in v2.
DOMAIN_ORDINALS_V1: Final[tuple[str, ...]] = (
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
)

DOMAINS: Final[frozenset[str]] = frozenset(DOMAIN_ORDINALS_V1)

CITATION_STATE_A_ENFORCED: Final[str] = (
    "Framework v1 — SAMVAAD.a enforced; reference rubrics v1.1; "
    "matcher spec v1; invariants I-S-1…15 frozen"
)

# Grep-visible anchor in test artifacts / fixture exports (not docs-only).
FRAMEWORK_CITATION_ANCHOR: Final[str] = CITATION_STATE_A_ENFORCED

MICRO_COMPETENCY_SCOPING_TARGET: Final[tuple[int, int]] = (40, 55)


def assert_domain_ordinal_stable() -> None:
    """v1: DOMAINS must match DOMAIN_ORDINALS_V1 exactly (no gaps, no reorder)."""
    if frozenset(DOMAIN_ORDINALS_V1) != DOMAINS:
        raise ValueError(
            f"DOMAIN_ORDINALS_V1 {DOMAIN_ORDINALS_V1} != DOMAINS {sorted(DOMAINS)}"
        )
    if len(DOMAIN_ORDINALS_V1) != len(DOMAINS):
        raise ValueError("domain ordinal set size mismatch")


def next_additive_domain_letter(current: tuple[str, ...]) -> str:
    """v2+ extension discipline: append next Latin letter only."""
    if not current:
        raise ValueError("empty domain ordinal list")
    last = current[-1]
    if last == "Z":
        raise ValueError("domain alphabet exhausted")
    return chr(ord(last) + 1)
