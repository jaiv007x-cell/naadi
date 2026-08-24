"""Dry-run executor — golden fixtures through Nirikshak."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.authoring.blueprint_io import (
    content_hash_from_case_json,
    grading_blueprint_from_case_json,
)
from services.pratibimb.authoring.constants import FixtureKind
from services.pratibimb.authoring.models import GoldenFixtureRow
from services.pratibimb.authoring.trace_io import trace_from_json


def _blueprint_hash(blueprint_json: dict) -> str:
    try:
        return content_hash_from_case_json(blueprint_json)
    except (KeyError, TypeError, ValueError):
        canonical = json.dumps(
            blueprint_json, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()



@dataclass(frozen=True)
class FixtureDryRunOutcome:
    fixture_kind: FixtureKind
    expected_passed: bool
    actual_passed: bool

    @property
    def matched_expectation(self) -> bool:
        return self.expected_passed == self.actual_passed


@dataclass(frozen=True)
class DryRunResult:
    outcomes: tuple[FixtureDryRunOutcome, ...]
    blueprint_hash: str
    fixture_hashes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(o.matched_expectation for o in self.outcomes)

    @property
    def result_hash(self) -> str:
        payload = {
            "outcomes": [
                {
                    "fixture_kind": o.fixture_kind.value,
                    "expected_passed": o.expected_passed,
                    "actual_passed": o.actual_passed,
                    "matched": o.matched_expectation,
                }
                for o in sorted(self.outcomes, key=lambda o: o.fixture_kind.value)
            ],
            "passed": self.passed,
            "blueprint_hash": self.blueprint_hash,
            "fixture_hashes": list(self.fixture_hashes),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_dry_run(
    blueprint_json: dict,
    fixtures: list[GoldenFixtureRow],
) -> DryRunResult:
    grading = grading_blueprint_from_case_json(blueprint_json)
    outcomes: list[FixtureDryRunOutcome] = []

    for fixture in fixtures:
        kind = FixtureKind(fixture.fixture_kind)
        trace = trace_from_json(fixture.trace_json)
        grade = Nirikshak(grading, trace).grade()
        expected_passed = bool(fixture.expected_grade_json.get("passed"))
        outcomes.append(
            FixtureDryRunOutcome(
                fixture_kind=kind,
                expected_passed=expected_passed,
                actual_passed=grade.passed,
            )
        )

    return DryRunResult(
        outcomes=tuple(outcomes),
        blueprint_hash=_blueprint_hash(blueprint_json),
        fixture_hashes=tuple(sorted(fx.content_hash for fx in fixtures)),
    )
