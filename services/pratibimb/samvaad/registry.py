"""SAMVAAD.c — merge SAMVAAD matchers into Nirikshak registry."""
from __future__ import annotations

from services.pratibimb.app.eval import matcher_registry
from services.pratibimb.samvaad.matcher_kinds import SAMVAAD_MATCHER_KINDS
from services.pratibimb.samvaad.nirikshak_matchers import SAMVAAD_MATCHERS


def merged_matcher_kinds() -> frozenset[str]:
    return frozenset(set(matcher_registry.KNOWN_MATCHER_KINDS) | set(SAMVAAD_MATCHER_KINDS))


def assert_samvaad_registry_exact() -> None:
    assert set(SAMVAAD_MATCHERS.keys()) == set(SAMVAAD_MATCHER_KINDS)
    assert len(SAMVAAD_MATCHERS) == len(SAMVAAD_MATCHER_KINDS)


def apply_samvaad_registry_merge() -> None:
    """Union SAMVAAD kinds into matcher_registry (idempotent)."""
    assert_samvaad_registry_exact()
    merged = merged_matcher_kinds()
    matcher_registry.KNOWN_MATCHER_KINDS = merged  # type: ignore[misc]
