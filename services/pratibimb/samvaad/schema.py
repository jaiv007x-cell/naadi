"""SAMVAAD rubric / summative payload validation (Framework v1 I-S-*)."""
from __future__ import annotations

from typing import Any

from services.pratibimb.samvaad.constants import DOMAINS
from services.pratibimb.samvaad.fail_case_allowlist import FAIL_CASE_ALLOWLIST
from services.pratibimb.samvaad.invariants import (
    FORBIDDEN_PRESENTATION_FEATURES,
    is_biometric_field,
    is_patient_feedback_field,
    is_subjective_scale_key,
)
from services.pratibimb.samvaad.matcher_spec import (
    COMBINATOR,
    FORBIDDEN_CREDENTIAL_SIGNALS,
    MATCHER_KIND,
    REQUIRED_STEPS,
)


class SamvaadSchemaError(ValueError):
    """Rubric or summative payload violates SAMVAAD Framework v1 schema."""


def _walk_keys(obj: Any, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            keys.append(str(k))
            keys.extend(_walk_keys(v, path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            keys.extend(_walk_keys(item, f"{prefix}[{i}]"))
    return keys


def validate_summative_payload(payload: dict[str, Any]) -> None:
    """Reject biometric / patient-feedback / subjective keys in summative evidence."""
    for key in _walk_keys(payload):
        if is_subjective_scale_key(key):
            raise SamvaadSchemaError(f"I-S-2 subjective scale key forbidden: {key}")
        if is_biometric_field(key):
            raise SamvaadSchemaError(f"I-S-9 biometric field forbidden: {key}")
        if is_patient_feedback_field(key):
            raise SamvaadSchemaError(f"I-S-13 patient-feedback field forbidden: {key}")


def validate_reference_rubric(rubric: dict[str, Any]) -> None:
    """Dry-run validate a SAMVAAD reference / corpus rubric dict."""
    validate_summative_payload(rubric)

    domain = rubric.get("domain")
    if domain not in DOMAINS:
        raise SamvaadSchemaError(f"I-S-1 unknown domain: {domain!r}")

    competency = rubric.get("competency")
    if not isinstance(competency, str) or not competency:
        raise SamvaadSchemaError("competency id required")

    matcher = rubric.get("matcher")
    if not isinstance(matcher, dict) or "kind" not in matcher:
        raise SamvaadSchemaError("matcher.kind required")

    if rubric.get("fail_case_on_violation") is True:
        if competency not in FAIL_CASE_ALLOWLIST:
            raise SamvaadSchemaError(
                f"I-S-4 fail_case_on_violation not allowlisted: {competency}"
            )

    if competency == "comm.patient.register_match":
        params = rubric.get("params") or {}
        if params.get("forbid_language_detection_as_sole_pass") is not True:
            raise SamvaadSchemaError("I-S-5 Domain A must forbid language-only pass")
        if matcher.get("kind") == "language_detected":
            raise SamvaadSchemaError("I-S-5 language_detected cannot be sole matcher")

    if competency == "comm.empathy.acknowledge_distress":
        if matcher.get("kind") != MATCHER_KIND:
            raise SamvaadSchemaError(
                f"empathy matcher must be {MATCHER_KIND}, got {matcher.get('kind')}"
            )
        params = rubric.get("params") or {}
        if params.get("combinator") != COMBINATOR:
            raise SamvaadSchemaError(
                f"I-S-6 empathy combinator must be {COMBINATOR!r}"
            )
        required = matcher.get("required") or params.get("required")
        if not isinstance(required, list):
            raise SamvaadSchemaError("empathy matcher requires action_sequence steps")
        missing = [s for s in REQUIRED_STEPS if s not in required]
        if missing:
            raise SamvaadSchemaError(f"empathy matcher missing steps: {missing}")
        for signal in FORBIDDEN_CREDENTIAL_SIGNALS:
            if signal in _walk_keys(rubric):
                raise SamvaadSchemaError(
                    f"empathy must not use credential signal: {signal}"
                )

    if rubric.get("domain") == "G":
        forbidden = set(rubric.get("forbidden_features") or [])
        if not FORBIDDEN_PRESENTATION_FEATURES.issubset(forbidden):
            raise SamvaadSchemaError(
                "I-S-12 Domain G must declare full forbidden_features set"
            )


def validate_all_reference_rubrics(rubrics: tuple[dict[str, Any], ...]) -> None:
    for r in rubrics:
        validate_reference_rubric(r)
