"""
Matcher DSL compiler — phase B validation layer for authoring drafts.

Imports action and matcher registries from runtime modules (never duplicated).
Authoring mistakes return CompilerErrors; only programmer bugs raise.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.pratibimb.authoring.constants import CompileIssueCode, StructuralIssueCode
from services.pratibimb.authoring.registry_snapshot import RegistrySnapshot

_ACTION_ID_PARAM_KEYS = frozenset({"action_id"})


@dataclass(frozen=True)
class CompilerError:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class CompilerErrors:
    """Result aggregate from ``compile_blueprint`` — not an exception; do not raise or ``except`` it."""

    errors: tuple[CompilerError, ...]


@dataclass(frozen=True)
class CompiledBlueprint:
    hit_ids: tuple[str, ...]


CompileResult = CompiledBlueprint | CompilerErrors


def compile_blueprint(
    blueprint: dict,
    *,
    snapshot: RegistrySnapshot | None = None,
) -> CompileResult:
    """
    Validate matcher kinds, action IDs, and per-kind references.

    Never raises for authoring errors — returns CompilerErrors instead.
    When `snapshot` is set (submit-time pin), catalogs come from that freeze
    rather than live registries.
    """
    from services.pratibimb.app.action_registry import (
        DEPRECATED_ACTION_IDS,
        DEPRECATED_ACTION_RETIRED_ON,
        KNOWN_ACTION_IDS,
        resolve as resolve_live,
    )
    from services.pratibimb.app.eval.matcher_registry import KNOWN_MATCHER_KINDS

    action_ids = frozenset(snapshot.action_ids) if snapshot else KNOWN_ACTION_IDS
    matcher_kinds = frozenset(snapshot.matcher_kinds) if snapshot else KNOWN_MATCHER_KINDS
    deprecated = (
        snapshot.deprecated_action_ids if snapshot else DEPRECATED_ACTION_IDS
    )
    retired_dates = snapshot.retired_on if snapshot else DEPRECATED_ACTION_RETIRED_ON

    def resolve_action(action_id: str) -> str | None:
        if snapshot is not None:
            return snapshot.resolve_action(action_id)
        return resolve_live(action_id)
    raw = blueprint.get("grading_blueprint")
    if raw is None:
        return CompiledBlueprint(hit_ids=())

    errors: list[CompilerError] = []
    hits = raw.get("hits") or []
    seen_ids: set[str] = set()

    for index, hit in enumerate(hits):
        path = f"grading_blueprint.hits[{index}]"
        hit_id = hit.get("id")
        if not hit_id:
            errors.append(CompilerError(
                path=f"{path}.id",
                code=CompileIssueCode.MISSING_HIT_ID.value,
                message=f"{path}: id is required",
            ))
            continue

        if hit_id in seen_ids:
            errors.append(CompilerError(
                path=f"{path}.id",
                code=CompileIssueCode.DUPLICATE_HIT_ID.value,
                message=f"grading_blueprint: duplicate hit id {hit_id!r}",
            ))
        seen_ids.add(str(hit_id))

        matcher = hit.get("matcher")
        matcher_path = f"{path}.matcher"
        if not matcher:
            errors.append(CompilerError(
                path=matcher_path,
                code=CompileIssueCode.MISSING_MATCHER.value,
                message=f"grading_blueprint.hits[{hit_id}]: matcher is required",
            ))
            continue

        if matcher == "custom":
            errors.append(CompilerError(
                path=matcher_path,
                code=CompileIssueCode.CUSTOM_MATCHER_FORBIDDEN.value,
                message=f"grading_blueprint.hits[{hit_id}]: matcher 'custom' is not allowed",
            ))
            continue

        if matcher not in matcher_kinds:
            errors.append(CompilerError(
                path=matcher_path,
                code=CompileIssueCode.UNKNOWN_MATCHER_KIND.value,
                message=(
                    f"grading_blueprint.hits[{hit_id}]: unknown matcher kind {matcher!r} "
                    f"(must be one of {sorted(matcher_kinds)})"
                ),
            ))

        points = hit.get("points", 1.0)
        points_path = f"{path}.points"
        try:
            points_f = float(points)
        except (TypeError, ValueError):
            errors.append(CompilerError(
                path=points_path,
                code=CompileIssueCode.INVALID_POINTS.value,
                message=f"grading_blueprint.hits[{hit_id}]: points must be a number",
            ))
            points_f = -1.0
        if points_f < 0:
            errors.append(CompilerError(
                path=points_path,
                code=CompileIssueCode.NEGATIVE_POINTS.value,
                message=(
                    f"grading_blueprint.hits[{hit_id}]: points must be >= 0 "
                    "(positive-evidence only)"
                ),
            ))

        params = hit.get("params") or {}
        for key in _ACTION_ID_PARAM_KEYS:
            action_id = params.get(key)
            if not action_id:
                continue
            action_path = f"{path}.params.{key}"
            raw_id = str(action_id)
            canonical = resolve_action(raw_id)
            if raw_id in deprecated and canonical is None:
                retired_day = retired_dates.get(raw_id)
                when = f" retired {retired_day}" if retired_day else " retired"
                errors.append(CompilerError(
                    path=action_path,
                    code=CompileIssueCode.DEPRECATED_ACTION_ID.value,
                    message=(
                        f"Action `{raw_id}`{when} with no replacement"
                    ),
                ))
            elif canonical is None and raw_id not in action_ids:
                errors.append(CompilerError(
                    path=action_path,
                    code=CompileIssueCode.UNREGISTERED_ACTION_ID.value,
                    message=(
                        f"grading_blueprint.hits[{hit_id}]: unregistered action id "
                        f"{raw_id!r}"
                    ),
                ))
            elif raw_id in deprecated and canonical:
                retired_day = retired_dates.get(raw_id)
                when = f" retired {retired_day}" if retired_day else " retired"
                errors.append(CompilerError(
                    path=action_path,
                    code=CompileIssueCode.DEPRECATED_ACTION_ID.value,
                    message=(
                        f"Action `{raw_id}`{when}; use `{canonical}`"
                    ),
                ))

    hit_ids = {str(h.get("id")) for h in hits if h.get("id")}
    for index, hit in enumerate(hits):
        hit_id = hit.get("id") or "?"
        path = f"grading_blueprint.hits[{index}]"
        params = hit.get("params") or {}

        inner = params.get("inner_hit")
        if hit.get("matcher") == "action_within" and inner:
            if str(inner) not in hit_ids:
                errors.append(CompilerError(
                    path=f"{path}.params.inner_hit",
                    code=CompileIssueCode.UNKNOWN_INNER_HIT.value,
                    message=(
                        f"grading_blueprint.hits[{hit_id}]: inner_hit {inner!r} "
                        "references unknown hit id"
                    ),
                ))

        if hit.get("matcher") == "sequence":
            steps = params.get("steps") or []
            for step_i, step in enumerate(steps):
                if str(step) not in hit_ids:
                    errors.append(CompilerError(
                        path=f"{path}.params.steps[{step_i}]",
                        code=CompileIssueCode.UNKNOWN_SEQUENCE_STEP.value,
                        message=(
                            f"grading_blueprint.hits[{hit_id}]: sequence step "
                            f"{step!r} references unknown hit id"
                        ),
                    ))

    if errors:
        return CompilerErrors(errors=tuple(errors))
    return CompiledBlueprint(hit_ids=tuple(sorted(hit_ids)))


def compile_rubric_errors(blueprint_json: dict) -> list[str]:
    """Return compile-time rubric error messages, worst first."""
    result = compile_blueprint(blueprint_json)
    if isinstance(result, CompiledBlueprint):
        return []
    return [err.message for err in result.errors]


def draft_validation_errors(
    blueprint_json: dict,
    *,
    snapshot: RegistrySnapshot | None = None,
) -> list[str]:
    """Structural case errors + rubric compile errors (save-time surface)."""
    return [issue["message"] for issue in draft_validation_issues(blueprint_json, snapshot=snapshot)]


def draft_validation_issues(
    blueprint_json: dict,
    *,
    snapshot: RegistrySnapshot | None = None,
) -> list[dict]:
    """
    Discriminated issues for the authoring UI.

    Each issue is ``{kind, code, path, message}``:

    - ``kind``: ``structural`` (case envelope) or ``compile`` (rubric DSL).
    - ``code``: ``StructuralIssueCode`` or ``CompileIssueCode`` wire value.
    - ``path``: dotted path from blueprint root — e.g.
      ``grading_blueprint.hits[0].matcher``, ``grading_blueprint.hits[2].params.action_id``.
      Not JSON Pointer (no leading ``/``); array indices in brackets.
    - ``message``: human-readable detail for display and flat ``validation_errors``.

    Flat ``validation_errors`` strings are derived from ``message`` so existing
    clients keep working.
    """
    from services.pratibimb.authoring.blueprint_io import validation_errors_from_case_json

    issues: list[dict] = []
    for message in validation_errors_from_case_json(blueprint_json):
        issues.append({
            "kind": "structural",
            "code": StructuralIssueCode.STRUCTURAL.value,
            "path": "",
            "message": message,
        })
    result = compile_blueprint(blueprint_json, snapshot=snapshot)
    if isinstance(result, CompilerErrors):
        for err in result.errors:
            issues.append({
                "kind": "compile",
                "code": err.code,
                "path": err.path,
                "message": err.message,
            })
    return issues
