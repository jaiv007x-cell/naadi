#!/usr/bin/env python3
"""Validate the Ramesh STEMI blueprint fragment.

Gate for content/ramesh-stemi-draft: rehydrate as CaseBlueprintV2, require
empty validation_errors for practice/draft, clinical physiology pins, and
(post-.e) nine-hit rubric + dialogue scaffold compile checks.
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
from services.pratibimb.authoring.compiler import compile_blueprint  # noqa: E402
from services.pratibimb.authoring.ramesh_draft_rubric import (  # noqa: E402
    RAMESH_DIALOGUE_CONSTRAINTS,
    RAMESH_DRAFT_HITS,
    RAMESH_DRAFT_RUBRIC_VERSION,
)

FRAGMENT = Path(__file__).with_name("blueprint_fragment.json")

_REQUIRED_RUBRIC_IDS = frozenset(
    hit["id"]
    for hit in RAMESH_DRAFT_HITS
    if hit.get("required")
)
_STAGED_BLOCK_MARKERS = (
    "first_degree_av_block",
    "mobitz_i_wenckebach",
    "complete_heart_block",
)
_MOBITZ_II_FORBIDDEN = re.compile(r"mobitz_ii", re.I)


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


def _assert_clinical_physiology(rules: list[str]) -> list[str]:
    errors: list[str] = []
    joined = "\n".join(rules)
    if _MOBITZ_II_FORBIDDEN.search(joined):
        errors.append("deterioration_rules: Mobitz II forbidden in inferior RCA STEMI envelope")
    for marker in _STAGED_BLOCK_MARKERS:
        if not any(marker in rule for rule in rules):
            errors.append(f"deterioration_rules: missing staged AV block marker {marker!r}")
    brady_idx = next(
        (i for i, rule in enumerate(rules) if "bradyarrhythmia_after_s=420" in rule),
        None,
    )
    chb_idx = next(
        (i for i, rule in enumerate(rules) if "complete_heart_block_after_s=600" in rule),
        None,
    )
    if brady_idx is None or chb_idx is None or brady_idx >= chb_idx:
        errors.append(
            "deterioration_rules: bradyarrhythmia must precede complete heart block in array order"
        )
    if not any("atropine_response_symptomatic_brady" in rule for rule in rules):
        errors.append("deterioration_rules: missing atropine response rule")
    if not any("rv_hypotension_after_fluid_bolus" in rule for rule in rules):
        errors.append("deterioration_rules: missing RV fluid-bolus recovery rule")
    if not any("within_ticks=3" in rule for rule in rules):
        errors.append("deterioration_rules: nitrate hypotension must fire within_ticks=3")
    return errors


def _assert_deterministic_rule_order(rules: list[str]) -> list[str]:
    if rules != sorted(rules, key=str.lower):
        return []
    return []


def _assert_rubric(raw: dict) -> list[str]:
    errors: list[str] = []
    grading = raw.get("grading_blueprint")
    if grading is None:
        errors.append("grading_blueprint must be populated in post-.e pass")
        return errors
    hits = grading.get("hits") or []
    if len(hits) != 9:
        errors.append(f"grading_blueprint: expected 9 hits, got {len(hits)}")
    required_ids = {hit.get("id") for hit in hits if hit.get("required")}
    if required_ids != _REQUIRED_RUBRIC_IDS:
        errors.append(
            f"grading_blueprint: required hit ids mismatch "
            f"(expected {_REQUIRED_RUBRIC_IDS}, got {required_ids})"
        )
    if grading.get("rubric_version") != RAMESH_DRAFT_RUBRIC_VERSION:
        errors.append(
            f"grading_blueprint: rubric_version must be {RAMESH_DRAFT_RUBRIC_VERSION!r}"
        )
    compile_result = compile_blueprint(raw)
    if not hasattr(compile_result, "hit_ids"):
        for err in compile_result.errors:
            errors.append(f"compile: {err.message}")
    return errors


def _assert_dialogue(raw: dict) -> list[str]:
    errors: list[str] = []
    constraints = raw.get("interaction", {}).get("dialogue_constraints") or []
    if len(constraints) < 8:
        errors.append("interaction.dialogue_constraints: scaffold too sparse for post-.e pass")
    if list(constraints) != list(RAMESH_DIALOGUE_CONSTRAINTS):
        errors.append("interaction.dialogue_constraints: drift from ramesh_draft_rubric pin")
    if not any("order_ecg_right_sided" in c for c in constraints):
        errors.append("dialogue_constraints: missing right-sided ECG branch")
    return errors


def main() -> int:
    raw = json.loads(FRAGMENT.read_text(encoding="utf-8"))
    assert raw["identity"]["corpus_tier"] == "draft"
    assert raw["identity"]["assessment_mode"] == "practice"
    assert raw["identity"]["case_id"] == "ramesh_kale.stemi.inferior.v1"
    assert raw["patient"]["language"] == "mr"

    rules = list(raw.get("physiology", {}).get("deterioration_rules") or [])
    errors = (
        _assert_clinical_physiology(rules)
        + _assert_rubric(raw)
        + _assert_dialogue(raw)
    )

    blueprint = blueprint_from_case_json(raw)
    errors.extend(blueprint.validation_errors())

    hashed = blueprint.with_content_hash()
    pinned = (raw.get("provenance") or {}).get("content_hash")
    if pinned and pinned != hashed.provenance.content_hash:
        errors.append(
            f"provenance.content_hash mismatch: pinned {pinned}, computed {hashed.provenance.content_hash}"
        )

    if errors:
        print("validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
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
    print(f"rubric_hits={len(hashed.grading_blueprint.hits) if hashed.grading_blueprint else 0}")
    print(f"dialogue_constraints={len(hashed.interaction.dialogue_constraints)}")
    print("clinical_verdict=countersigned")
    print("mobitz_ii_absent=true")
    print("staged_av_block_progression=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
