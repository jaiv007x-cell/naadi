"""Sealed-input pure grade for ARP (I-I-11). No grading-package import."""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Import-time constant — distinct string from H sealed-stub so envelopes differ.
GRADER_VERSION = "arp-sealed-stub.v1"

_grade_call_count = 0


def reset_grade_call_count() -> None:
    global _grade_call_count
    _grade_call_count = 0


def grade_call_count() -> int:
    return _grade_call_count


def grade_sealed_alternate(
    *,
    payload: dict[str, Any],
    alternate_rubric_id: str,
    alternate_rubric_version: str,
    transcript_digest: str,
) -> dict[str, Any]:
    """Pure function over sealed transcript + alternate rubric identity."""
    global _grade_call_count
    _grade_call_count += 1
    material = json.dumps(
        {
            "payload": payload,
            "alternate_rubric_id": alternate_rubric_id,
            "alternate_rubric_version": alternate_rubric_version,
            "transcript_digest": transcript_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    h = hashlib.sha256(material).hexdigest()
    score = int(h[:8], 16) % 1000 / 1000.0
    return {
        "grade_total": score,
        "grade_passed": score >= 0.5,
        "grader_version": GRADER_VERSION,
        "alternate_rubric_id": alternate_rubric_id,
        "alternate_rubric_version": alternate_rubric_version,
        "axis_normalized": {"competency": score},
    }
