"""Portable regrade envelope allowlist decode + offline verify (H.b)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.pratibimb.regrade.sign import (
    ARTIFACT_SCHEMA_V1,
    DEFAULT_REGRADE_ISSUER_KEY_ID,
    verify_regrade_signature,
)

# Closed field set — extra keys rejected (F.b.1 decoder discipline).
PORTABLE_ENVELOPE_ALLOWED_KEYS = frozenset(
    {
        "type",
        "artifact_schema_version",
        "regrade_id",
        "transcript_digest",
        "digest_alg",
        "grade_payload",
        "grader_version",
        "rubric_schema_version",
        "regraded_at",
        "signed_at",
        "valid_until",
        "regrade_issuer_key_id",
        "proof",
        "credential_id",  # optional G binding
    }
)

PROOF_ALLOWED_KEYS = frozenset({"type", "regrade_issuer_key_id", "signature"})


class EnvelopeReject(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class VerifyResult:
    accepted: bool
    reason: str = "ok"


def decode_portable_envelope(raw: dict[str, Any]) -> dict[str, Any]:
    """Allowlist decode — rejects unknown top-level and proof keys."""
    extra = set(raw.keys()) - PORTABLE_ENVELOPE_ALLOWED_KEYS
    if extra:
        raise EnvelopeReject(f"extra_keys:{sorted(extra)}")
    proof = raw.get("proof")
    if not isinstance(proof, dict):
        raise EnvelopeReject("missing_proof")
    proof_extra = set(proof.keys()) - PROOF_ALLOWED_KEYS
    if proof_extra:
        raise EnvelopeReject(f"extra_proof_keys:{sorted(proof_extra)}")
    if raw.get("artifact_schema_version") != ARTIFACT_SCHEMA_V1:
        raise EnvelopeReject("unknown_artifact_schema")
    if "session_id" in raw or "learner_pseudo_id" in raw:
        raise EnvelopeReject("forbidden_correlation_field")
    return raw


def offline_verify_regrade(
    envelope: dict[str, Any],
    *,
    now: datetime | None = None,
    expected_key_id: str = DEFAULT_REGRADE_ISSUER_KEY_ID,
) -> VerifyResult:
    now = now or datetime.now(timezone.utc)
    try:
        decoded = decode_portable_envelope(envelope)
    except EnvelopeReject as exc:
        return VerifyResult(accepted=False, reason=exc.reason)

    signed_at = datetime.fromisoformat(decoded["signed_at"].replace("Z", "+00:00"))
    valid_until = datetime.fromisoformat(decoded["valid_until"].replace("Z", "+00:00"))
    if valid_until <= signed_at:
        return VerifyResult(accepted=False, reason="valid_until_le_signed_at")

    kid = decoded.get("regrade_issuer_key_id") or (decoded.get("proof") or {}).get(
        "regrade_issuer_key_id"
    )
    if kid != expected_key_id:
        return VerifyResult(accepted=False, reason="unknown_regrade_key")

    if not verify_regrade_signature(decoded, key_id=kid):
        return VerifyResult(accepted=False, reason="signature_invalid")

    if now > valid_until:
        return VerifyResult(accepted=False, reason="envelope_expired")

    return VerifyResult(accepted=True, reason="ok")
