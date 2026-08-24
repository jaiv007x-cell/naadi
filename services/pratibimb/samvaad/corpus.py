"""SAMVAAD.b — in-memory rubric corpus over reference shapes (authoring DB later)."""
from __future__ import annotations

from typing import Any

from services.pratibimb.samvaad.constants import (
    DOMAIN_ORDINALS_V1,
    DOMAINS,
    REFERENCE_RUBRICS_VERSION,
)
from services.pratibimb.samvaad.reference.v1 import (
    CONTENT_HASH,
    reference_rubrics_v1,
)
from services.pratibimb.samvaad.schema import validate_all_reference_rubrics


class SamvaadCorpus:
    """Nine-domain corpus SoT for SAMVAAD.b — loads reference rubrics v1.1."""

    def __init__(self) -> None:
        rubrics = reference_rubrics_v1()
        validate_all_reference_rubrics(rubrics)
        self._rubrics = rubrics
        self.version = REFERENCE_RUBRICS_VERSION
        self.content_hash = CONTENT_HASH

    def all(self) -> tuple[dict[str, Any], ...]:
        return self._rubrics

    def by_domain(self, domain: str) -> list[dict[str, Any]]:
        if domain not in DOMAINS:
            raise ValueError(f"unknown domain {domain!r}; set={sorted(DOMAINS)}")
        return [r for r in self._rubrics if r["domain"] == domain]

    def domains_present(self) -> frozenset[str]:
        return frozenset(r["domain"] for r in self._rubrics)

    def assert_ordinals_stable(self) -> None:
        present = self.domains_present()
        if present != frozenset(DOMAIN_ORDINALS_V1):
            raise ValueError(
                f"corpus domains {sorted(present)} != DOMAIN_ORDINALS_V1 "
                f"{list(DOMAIN_ORDINALS_V1)}"
            )


def load_phase1_digital_literacy() -> list[dict[str, Any]]:
    """Domain H Phase 1 rubrics — compile/dry-run surface."""
    return SamvaadCorpus().by_domain("H")
