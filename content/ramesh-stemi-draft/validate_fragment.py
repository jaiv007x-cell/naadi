#!/usr/bin/env python3
"""Validate the Ramesh STEMI persona/physiology blueprint fragment.

Gate for content/ramesh-stemi-draft: rehydrate as CaseBlueprintV2, require
empty validation_errors for practice/draft, and refuse dialogue/rubric leak.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from services.pratibimb.authoring.blueprint_io import (  # noqa: E402
    blueprint_from_case_json,
)

FRAGMENT = Path(__file__).with_name("blueprint_fragment.json")

# Rubric / dialogue surfaces that must not appear in this pass's strings.
_LEAK_PATTERNS = (
    re.compile(r"\bwindow_ms\b", re.I),
    re.compile(r"\bmin_occurrences_per_turn\b", re.I),
    re.compile(r"\ballow_implicit_cue\b", re.I),
    re.compile(r"\bempathy_cue\b", re.I),
    re.compile(r"\brubric_hit\b", re.I),
    re.compile(r"\bstemi\.ecg_ordered\b", re.I),
    re.compile(r"\bstemi\.aspirin_chewed\b", re.I),
    re.compile(r"\bstemi\.pci_transfer\b", re.I),
    re.compile(r"\bdialogue_tree\b", re.I),
    re.compile(r"\bturn_graph\b", re.I),
)


def _walk_strings(obj, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.extend(_walk_strings(value, f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            found.extend(_walk_strings(value, f"{path}[{idx}]"))
    elif isinstance(obj, str):
        found.append((path, obj))
    return found


def main() -> int:
    raw = json.loads(FRAGMENT.read_text(encoding="utf-8"))
    assert raw.get("grading_blueprint") is None, "grading_blueprint must stay null this pass"
    assert raw.get("interaction", {}).get("dialogue_constraints") == [], (
        "dialogue_constraints must stay empty this pass"
    )
    assert raw["identity"]["corpus_tier"] == "draft"
    assert raw["identity"]["assessment_mode"] == "practice"
    assert raw["identity"]["case_id"] == "ramesh_kale.stemi.inferior.v1"
    assert raw["patient"]["language"] == "mr"
    assert raw["patient"]["demographics"]["native_language"] == "mr"

    blueprint = blueprint_from_case_json(raw)
    errors = blueprint.validation_errors()
    if errors:
        print("validation_errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    hashed = blueprint.with_content_hash()
    leaks: list[str] = []
    for path, text in _walk_strings(raw):
        for pattern in _LEAK_PATTERNS:
            if pattern.search(text):
                leaks.append(f"{path}: matched {pattern.pattern}")
    if leaks:
        print("dialogue/rubric leak:", file=sys.stderr)
        for item in leaks:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print("ok")
    print(f"case_id={hashed.case_id}")
    print(f"version={hashed.version}")
    print(f"corpus_tier={hashed.corpus_tier.value}")
    print(f"assessment_mode={hashed.assessment_mode.value}")
    print(f"content_hash={hashed.provenance.content_hash}")
    print(f"patient={hashed.patient.demographics.name} / {hashed.patient.language.value}")
    print(
        "physiology.initial_vitals="
        f"hr={hashed.physiology.initial_vitals.hr} "
        f"sbp={hashed.physiology.initial_vitals.sbp} "
        f"spo2={hashed.physiology.initial_vitals.spo2}"
    )
    print(f"deterioration_rules={len(hashed.physiology.deterioration_rules)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
