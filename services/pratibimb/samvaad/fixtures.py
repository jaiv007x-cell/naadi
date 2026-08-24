"""SAMVAAD acceptance fixture export — citation anchor grep-visible in test artifacts."""
from __future__ import annotations

from typing import Any

from services.pratibimb.samvaad.constants import (
    CITATION_STATE_A_ENFORCED,
    DOMAIN_ORDINALS_V1,
    DOMAIN_SET_VERSION,
    FRAMEWORK_CITATION_ANCHOR,
    REFERENCE_RUBRICS_VERSION,
)
from services.pratibimb.samvaad.matcher_spec import (
    EMPATHY_CUE_WINDOW_MS,
    MATCHER_SPEC_VERSION,
)
from services.pratibimb.samvaad.reference.v1 import CONTENT_HASH


def framework_acceptance_fixture() -> dict[str, Any]:
    """Serialized export for acceptance tests — anchor must match docs."""
    return {
        "framework_citation_anchor": FRAMEWORK_CITATION_ANCHOR,
        "citation_state": CITATION_STATE_A_ENFORCED,
        "domain_set_version": DOMAIN_SET_VERSION,
        "domain_ordinals_v1": list(DOMAIN_ORDINALS_V1),
        "reference_rubrics_version": REFERENCE_RUBRICS_VERSION,
        "reference_content_hash": CONTENT_HASH,
        "matcher_spec_version": MATCHER_SPEC_VERSION,
        "empathy_cue_window_ms": EMPATHY_CUE_WINDOW_MS,
    }
