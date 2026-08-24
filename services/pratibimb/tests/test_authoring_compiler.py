"""Phase B matcher DSL compiler — registry imports and validation_errors surface."""
from __future__ import annotations

import pytest

from services.pratibimb.app.action_registry import KNOWN_ACTION_IDS
from services.pratibimb.app.eval.matcher_registry import KNOWN_MATCHER_KINDS
from services.pratibimb.app.eval.nirikshak import Nirikshak
from services.pratibimb.app.eval.rubric import Axis, Severity
from services.pratibimb.authoring.compiler import compile_rubric_errors, draft_validation_errors
from services.pratibimb.authoring.ramesh_smoke import attach_smoke_rubric
from scripts.migrate_cases_v1_to_v2 import migrate_case_v1_to_v2
from services.pratibimb.tests.test_case_migration import _v1


def test_known_matcher_kinds_match_nirikshak_registry():
    from services.pratibimb.app.eval.rubric import GradingBlueprint
    from shared.schemas.trace import PhysioTrace

    n = Nirikshak(
        GradingBlueprint(
            case_id="x", case_version="1", rubric_version="0.3", hits=()
        ),
        PhysioTrace(),
    )
    assert set(n._matchers.keys()) == set(KNOWN_MATCHER_KINDS)


def test_ramesh_smoke_rubric_compiles_clean():
    result = migrate_case_v1_to_v2(_v1())
    assert result.blueprint is not None
    payload = attach_smoke_rubric(result.blueprint.to_dict())
    assert compile_rubric_errors(payload) == []


def test_unknown_matcher_kind_surfaces_in_validation_errors():
    payload = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    payload["grading_blueprint"]["hits"].append(
        {
            "id": "bad.matcher",
            "axis": Axis.ACTION.value,
            "matcher": "teleport",
            "params": {},
            "points": 1.0,
        }
    )
    errors = draft_validation_errors(payload)
    assert any("unknown matcher kind 'teleport'" in e for e in errors)


def test_custom_matcher_rejected():
    payload = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    payload["grading_blueprint"]["hits"][0]["matcher"] = "custom"
    errors = compile_rubric_errors(payload)
    assert any("custom" in e and "not allowed" in e for e in errors)


def test_compile_blueprint_is_discriminated_union():
    from services.pratibimb.authoring.compiler import (
        CompiledBlueprint,
        CompilerErrors,
        compile_blueprint,
    )

    good = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    result = compile_blueprint(good)
    assert isinstance(result, CompiledBlueprint)

    good["grading_blueprint"]["hits"][0]["matcher"] = "teleport"
    bad = compile_blueprint(good)
    assert isinstance(bad, CompilerErrors)
    assert bad.errors[0].code == "UNKNOWN_MATCHER_KIND"
    assert bad.errors[0].path.endswith(".matcher")


def test_unregistered_action_id_surfaces():
    payload = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    payload["grading_blueprint"]["hits"].append(
        {
            "id": "timing.ecg",
            "axis": Axis.TIMING.value,
            "matcher": "action_within",
            "params": {"action_id": "not_a_real_action", "within_s": 60},
            "points": 2.0,
        }
    )
    errors = compile_rubric_errors(payload)
    assert any("unregistered action id" in e for e in errors)


def test_compile_against_pinned_snapshot_ignores_live_registry():
    from services.pratibimb.authoring.compiler import CompiledBlueprint, compile_blueprint
    from services.pratibimb.authoring.registry_snapshot import RegistrySnapshot

    payload = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    payload["grading_blueprint"]["hits"].append(
        {
            "id": "timing.check",
            "axis": Axis.TIMING.value,
            "matcher": "action_within",
            "params": {
                "action_id": "give_aspirin_325_chewed",
                "within_s": 60,
                "inner_hit": "stemi.ecg_ordered",
            },
            "points": 2.0,
        }
    )
    frozen = RegistrySnapshot(
        action_ids=("give_aspirin_325_chewed",),
        matcher_kinds=("action_within", "drug_given", "order_placed", "escalation"),
        deprecated_action_ids={},
        retired_on={},
        guideline_versions=(),
        nirikshak_version="0.3.0",
        harness_version="0.1.0",
    )
    empty_live = RegistrySnapshot(
        action_ids=(),
        matcher_kinds=("action_within", "drug_given", "order_placed", "escalation"),
        deprecated_action_ids={},
        retired_on={},
        guideline_versions=(),
        nirikshak_version="0.3.0",
        harness_version="0.1.0",
    )
    assert isinstance(compile_blueprint(payload, snapshot=frozen), CompiledBlueprint)
    bad = compile_blueprint(payload, snapshot=empty_live)
    from services.pratibimb.authoring.compiler import CompilerErrors

    assert isinstance(bad, CompilerErrors)


def test_known_action_id_passes_compile():
    known = next(iter(KNOWN_ACTION_IDS))
    payload = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    payload["grading_blueprint"]["hits"].append(
        {
            "id": "timing.check",
            "axis": Axis.TIMING.value,
            "matcher": "action_within",
            "params": {"action_id": known, "within_s": 60, "inner_hit": "stemi.ecg_ordered"},
            "points": 2.0,
        }
    )
    errors = compile_rubric_errors(payload)
    assert not any("unregistered action id" in e for e in errors)


def test_negative_points_rejected():
    result = migrate_case_v1_to_v2(_v1())
    assert result.blueprint is not None
    payload = attach_smoke_rubric(result.blueprint.to_dict())
    payload["grading_blueprint"]["hits"][0]["points"] = -1.0
    errors = compile_rubric_errors(payload)
    assert any("points must be >= 0" in e for e in errors)


def test_deprecated_action_id_names_replacement():
    payload = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    payload["grading_blueprint"]["hits"].append(
        {
            "id": "timing.legacy",
            "axis": Axis.TIMING.value,
            "matcher": "action_within",
            "params": {"action_id": "give_aspirin", "within_s": 60, "inner_hit": "stemi.ecg_ordered"},
            "points": 2.0,
        }
    )
    errors = compile_rubric_errors(payload)
    assert any("give_aspirin" in e and "give_aspirin_325_chewed" in e for e in errors)
    assert any("2025-11-03" in e for e in errors)


def test_validation_issues_are_discriminated():
    from services.pratibimb.authoring.compiler import draft_validation_issues

    payload = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    payload["grading_blueprint"]["hits"][0]["matcher"] = "teleport"
    issues = draft_validation_issues(payload)
    compile_issues = [i for i in issues if i["kind"] == "compile"]
    assert compile_issues
    assert compile_issues[0]["code"] == "UNKNOWN_MATCHER_KIND"


def test_duplicate_hit_id_rejected():
    payload = attach_smoke_rubric(migrate_case_v1_to_v2(_v1()).blueprint.to_dict())
    payload["grading_blueprint"]["hits"].append(dict(payload["grading_blueprint"]["hits"][0]))
    errors = compile_rubric_errors(payload)
    assert any("duplicate hit id" in e for e in errors)
