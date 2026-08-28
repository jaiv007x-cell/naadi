#!/usr/bin/env python3
"""Validate the Aarav neonatal sepsis blueprint fragment.

Gate for content/aarav-sepsis-draft: rehydrate as CaseBlueprintV2, clinical
physiology pins (hypothermia flip, proxy informant), persona pass with rubric
and dialogue parked.
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

_EXPECTED_RULE_COUNT = 26
_HYPOTHERMIA_FLIP = "untreated_temp_flip_hypothermia"
_PROXY_INFORMANT = "informant_mother_meera"

_LEAK_PATTERNS = (
    re.compile(r"\bwindow_ms\b", re.I),
    re.compile(r"\brubric_hit\b", re.I),
    re.compile(r"\bsepsis\.empiric_abx\b", re.I),
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


def _assert_clinical_physiology(rules: list[str], raw: dict) -> list[str]:
    errors: list[str] = []
    if len(rules) != _EXPECTED_RULE_COUNT:
        errors.append(
            f"deterioration_rules: expected {_EXPECTED_RULE_COUNT}, got {len(rules)}"
        )
    if not any(_HYPOTHERMIA_FLIP in rule for rule in rules):
        errors.append("deterioration_rules: missing hypothermia-flip trap (misread-as-improving)")
    if not any("fluid_bolus_10ml_kg_ns" in rule for rule in rules):
        errors.append("deterioration_rules: missing correct neonatal 10 ml/kg fluid rule")
    if not any("fluid_bolus_20ml_kg" in rule for rule in rules):
        errors.append("deterioration_rules: missing PALS-reflex 20 ml/kg iatrogenic trap")
    if not any("subtle_neonatal_seizure" in rule for rule in rules):
        errors.append("deterioration_rules: missing subtle neonatal seizure branch")

    interaction = raw.get("interaction") or {}
    if _PROXY_INFORMANT not in (interaction.get("family_personas") or []):
        errors.append("interaction: proxy informant Meera must drive dialogue surface")
    prom_hidden = any("PROM" in fact for fact in (interaction.get("hidden_facts") or []))
    if not prom_hidden:
        errors.append("interaction.hidden_facts: maternal PROM risk must be buried for elicitation")

    return errors


def main() -> int:
    raw = json.loads(FRAGMENT.read_text(encoding="utf-8"))
    assert raw.get("grading_blueprint") is None, "grading_blueprint must stay null this pass"
    assert raw.get("interaction", {}).get("dialogue_constraints") == [], (
        "dialogue_constraints must stay empty this pass"
    )
    assert raw["identity"]["case_id"] == "baby_aarav.neonatal_sepsis.eos.v1"
    assert raw["identity"]["corpus_tier"] == "draft"
    assert raw["patient"]["language"] == "ta"

    rules = list(raw.get("physiology", {}).get("deterioration_rules") or [])
    errors = _assert_clinical_physiology(rules, raw)

    blueprint = blueprint_from_case_json(raw)
    errors.extend(blueprint.validation_errors())

    hashed = blueprint.with_content_hash()
    pinned = (raw.get("provenance") or {}).get("content_hash")
    if pinned and pinned != hashed.provenance.content_hash:
        errors.append(
            f"provenance.content_hash mismatch: pinned {pinned}, "
            f"computed {hashed.provenance.content_hash}"
        )

    leaks: list[str] = []
    for path, text in _walk_strings(raw):
        for pattern in _LEAK_PATTERNS:
            if pattern.search(text):
                leaks.append(f"{path}: matched {pattern.pattern}")
    if leaks:
        errors.extend(leaks)

    if errors:
        print("validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("ok")
    print(f"case_id={hashed.case_id}")
    print(f"version={hashed.version}")
    print(f"content_hash={hashed.provenance.content_hash}")
    print(f"deterioration_rules={len(hashed.physiology.deterioration_rules)}")
    print("proxy_informant=true")
    print("hypothermia_flip_pinned=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
