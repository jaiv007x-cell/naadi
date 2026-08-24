"""Portable ARP envelope allowlist decode + offline verify (I.b)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.pratibimb.arp.sign import (
    ARTIFACT_SCHEMA_V1,
    DEFAULT_ARP_ISSUER_KEY_ID,
    verify_arp_signature,
)

PORTABLE_ARP_ALLOWED_KEYS = frozenset(
    {
        "type",
        "artifact_schema_version",
        "arp_id",
        "transcript_digest",
        "digest_alg",
        "score_payload",
        "grader_version",
        "sealed_rubric_id",
        "transcript_rubric_schema_version",
        "alternate_rubric_id",
        "alternate_rubric_version",
        "authority_id",
        "recomputed_at",
        "signed_at",
        "valid_until",
        "arp_issuer_key_id",
        "proof",
        "regrade_id",
        "credential_id",
    }
)

PROOF_ALLOWED_KEYS = frozenset({"type", "arp_issuer_key_id", "signature"})


class EnvelopeReject(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class VerifyResult:
    accepted: bool
    reason: str = "ok"

    def as_body(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "reason": self.reason}


def decode_portable_arp_envelope(raw: dict[str, Any]) -> dict[str, Any]:
    extra = set(raw.keys()) - PORTABLE_ARP_ALLOWED_KEYS
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


def offline_verify_arp(
    envelope: dict[str, Any],
    *,
    now: datetime | None = None,
    expected_key_id: str = DEFAULT_ARP_ISSUER_KEY_ID,
    status_list: dict[str, Any] | None = None,
) -> VerifyResult:
    now = now or datetime.now(timezone.utc)
    try:
        decoded = decode_portable_arp_envelope(envelope)
    except EnvelopeReject as exc:
        return VerifyResult(accepted=False, reason=exc.reason)

    signed_at = datetime.fromisoformat(decoded["signed_at"].replace("Z", "+00:00"))
    valid_until = datetime.fromisoformat(decoded["valid_until"].replace("Z", "+00:00"))
    if valid_until <= signed_at:
        return VerifyResult(accepted=False, reason="valid_until_le_signed_at")

    kid = decoded.get("arp_issuer_key_id") or (decoded.get("proof") or {}).get(
        "arp_issuer_key_id"
    )
    if kid != expected_key_id:
        return VerifyResult(accepted=False, reason="unknown_arp_key")

    if not verify_arp_signature(decoded, key_id=kid):
        return VerifyResult(accepted=False, reason="signature_invalid")

    if now > valid_until:
        return VerifyResult(accepted=False, reason="envelope_expired")

    if status_list is not None:
        arp_id = decoded.get("arp_id")
        for entry in status_list.get("entries") or []:
            kind = entry.get("identifier_kind")
            if kind is None and entry.get("credential_id"):
                kind = "credential"
            eid = entry.get("identifier_id") or entry.get("credential_id")
            if kind == "arp" and eid == arp_id and entry.get("revoked_at"):
                return VerifyResult(accepted=False, reason="arp_revoked")

    return VerifyResult(accepted=True, reason="ok")
