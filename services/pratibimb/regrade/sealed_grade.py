"""Sealed-input pure grade function for H.a/H.b (I-H-13).

Does **not** import the grading package — deterministic stub over sealed
transcript payload + sealed rubric version. Real grader wire lands behind this
adapter shape in a later slice without reversing isolation greps.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Import-time constant only — not env/runtime config (H.b C3).
GRADER_VERSION = "sealed-stub.v1"

# Test/observability: increments on every grade_sealed invocation (I-H-11 belt).
_grade_call_count = 0


def reset_grade_call_count() -> None:
    global _grade_call_count
    _grade_call_count = 0


def grade_call_count() -> int:
    return _grade_call_count


def grade_sealed(
    *,
    payload: dict[str, Any],
    rubric_schema_version: str,
    transcript_digest: str,
) -> dict[str, Any]:
    """Pure function: sealed inputs → grade payload (no mutable grading state)."""
    global _grade_call_count
    _grade_call_count += 1
    material = json.dumps(
        {
            "payload": payload,
            "rubric_schema_version": rubric_schema_version,
            "transcript_digest": transcript_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    h = hashlib.sha256(material).hexdigest()
    score = int(h[:8], 16) % 1000 / 1000.0
    passed = score >= 0.5
    return {
        "grade_total": score,
        "grade_passed": passed,
        "grader_version": GRADER_VERSION,
        "rubric_schema_version": rubric_schema_version,
        "axis_normalized": {"competency": score},
    }
