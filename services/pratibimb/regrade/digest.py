"""Transcript digest for regrade (I-H-11).

Digest algorithm inherited from grader-digest brief track — same placeholder as
G credentials until the brief pin freezes production alg (I-G-3 / I-G-7 / I-H-11).
Do not invent a second default.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Shared with credentials.digest — brief-track interlock (I-H-11).
DIGEST_ALG_PLACEHOLDER = "sha256-canonical-json-v0"


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def compute_transcript_digest(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (hex_digest, digest_alg) over allowlisted projection payload."""
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return digest, DIGEST_ALG_PLACEHOLDER
