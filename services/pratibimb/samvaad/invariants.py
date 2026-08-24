"""I-S-3 / I-S-10 / I-S-13 deny-lists and structural-absence patterns.

Named enforcement mechanisms — not policy prose.
"""
from __future__ import annotations

import re
from typing import Final

# I-S-3 — subjective attitude / personality scales (exact keys + prefixes)
SUBJECTIVE_SCALE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "attitude",
        "professionalism_score",
        "empathy_score",
        "personality_score",
        "soft_skill_score",
        "communication_score_1_10",
        "communication_score_scalar",
    }
)
SUBJECTIVE_SCALE_PREFIXES: Final[tuple[str, ...]] = (
    "personality_",
    "attitude_",
)

# I-S-10 — biometric / inferred-affect tokens (summative / credential / BEEMA)
BIOMETRIC_FIELD_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^heart_rate$", re.I),
    re.compile(r"^hrv(_|$)", re.I),
    re.compile(r"^facial_emotion", re.I),
    re.compile(r"^biometric_", re.I),
    re.compile(r"^inferred_affect", re.I),
    re.compile(r"^gsr(_|$)", re.I),
    re.compile(r"^pupil_", re.I),
    re.compile(r"^voice_tone_emotion", re.I),
    re.compile(r"^eda(_|$)", re.I),
)

# Raw identifiers for AST / text grep belts (credentials, beema)
BIOMETRIC_GREP_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "heart_rate",
        "facial_emotion",
        "biometric_",
        "inferred_affect",
        "voice_tone_emotion",
        "samvaad.biometric_summative",
    }
)

# I-S-13 — patient feedback deferred
PATIENT_FEEDBACK_FIELD_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^patient_feedback", re.I),
    re.compile(r"^patient_rating", re.I),
    re.compile(r"^patient_nps", re.I),
    re.compile(r"^pt_satisfaction", re.I),
)

PATIENT_FEEDBACK_GREP_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "patient_feedback",
        "patient_rating",
        "patient_nps",
        "pt_satisfaction",
    }
)

FORBIDDEN_PRESENTATION_FEATURES: Final[frozenset[str]] = frozenset(
    {
        "attractiveness",
        "accent",
        "skin_tone",
        "polish",
        "class_coded_mannerism",
        "expensive_clothing",
    }
)


def key_matches_patterns(key: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(key) for p in patterns)


def is_subjective_scale_key(key: str) -> bool:
    if key in SUBJECTIVE_SCALE_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in SUBJECTIVE_SCALE_PREFIXES)


def is_biometric_field(key: str) -> bool:
    return key_matches_patterns(key, BIOMETRIC_FIELD_PATTERNS)


def is_patient_feedback_field(key: str) -> bool:
    return key_matches_patterns(key, PATIENT_FEEDBACK_FIELD_PATTERNS)
