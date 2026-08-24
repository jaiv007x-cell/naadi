"""Projection digest for credential binding (I-G-3).

Digest covers F-projection allowlisted fields. ``session_id`` is included in the
hashed payload for content binding but is never emitted in the portable VC body.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from shared.schemas.ledger_read import NcvetSessionEvidenceView

# Placeholder until strategy-brief digest pin freezes production alg (I-G-7).
DIGEST_ALG_PLACEHOLDER = "sha256-canonical-json-v0"


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def projection_digest_payload(view: NcvetSessionEvidenceView) -> dict[str, Any]:
    """Allowlisted fields from F projection view (I-F-5 / I-G-3)."""
    return {
        "session_id": view.session_id,
        "tenant_id": view.tenant_id,
        "learner_pseudo_id": view.learner_pseudo_id,
        "cohort_id": view.cohort_id,
        "case_id": view.case_id,
        "case_version": view.case_version,
        "finalized_at_utc": view.finalized_at_utc.isoformat(),
        "physio_engine_version": view.physio_engine_version,
        "rubric_version": view.rubric_version,
        "replay_hash": view.replay_hash,
        "blueprint_content_hash": view.blueprint_content_hash,
        "blueprint_source": view.blueprint_source,
        "grade_total": view.grade_total,
        "grade_passed": view.grade_passed,
        "axis_normalized": view.axis_normalized,
        "evidence": view.evidence,
        "flags": view.flags,
        "actions": view.actions,
        "case_context": view.case_context,
        "schema_version": view.schema_version,
    }


def compute_projection_digest(view: NcvetSessionEvidenceView) -> tuple[str, str]:
    """Return (hex_digest, digest_alg)."""
    payload = projection_digest_payload(view)
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return digest, DIGEST_ALG_PLACEHOLDER
