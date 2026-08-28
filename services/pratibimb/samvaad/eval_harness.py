"""Minimal Nirikshak harness for SAMVAAD matcher dispatch tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.pratibimb.app.eval import nirikshak as nirikshak_mod
from services.pratibimb.samvaad.nirikshak_matchers import SAMVAAD_MATCHERS
from services.pratibimb.samvaad.runtime import validate_matcher_params


@dataclass
class SamvaadRubricHit:
    params: dict[str, Any]
    matcher: dict[str, Any]

    def __post_init__(self) -> None:
        validate_matcher_params(self.params)


class SamvaadEvalHarness:
    def __init__(self, samvaad_context: dict[str, Any] | None = None) -> None:
        self.samvaad_context = samvaad_context or {}

    def evaluate_kind(self, kind: str, hit: SamvaadRubricHit) -> tuple[bool, dict]:
        matcher = SAMVAAD_MATCHERS[kind]
        return matcher(self, hit)


def production_matchers() -> dict[str, Any]:
    return dict(nirikshak_mod._MATCHERS)
