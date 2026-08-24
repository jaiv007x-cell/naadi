"""I.a portable ARP envelope signing — distinct arp_issuer_key_id (I-I-12)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_ARP_ISSUER_KEY_ID = "dev-arp-issuer-key-1"
ARTIFACT_SCHEMA_V1 = "arp_artifact.v1"
DEFAULT_ENVELOPE_TTL = timedelta(days=365)


def _arp_signing_secret(key_id: str) -> bytes:
    base = os.getenv("CREDENTIAL_ARP_HMAC_SECRET", "dev-only-arp-issuer-secret")
    return hmac.new(base.encode("utf-8"), key_id.encode("utf-8"), hashlib.sha256).digest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def sign_arp_claims(
    claims: dict[str, Any],
    *,
    key_id: str = DEFAULT_ARP_ISSUER_KEY_ID,
) -> dict[str, Any]:
    body = {k: v for k, v in claims.items() if k != "proof"}
    sig = hmac.new(_arp_signing_secret(key_id), _canonical(body), hashlib.sha256).hexdigest()
    return {
        **body,
        "proof": {
            "type": "JsonHmac2026",
            "arp_issuer_key_id": key_id,
            "signature": sig,
        },
    }


def verify_arp_signature(envelope: dict[str, Any], *, key_id: str | None = None) -> bool:
    proof = envelope.get("proof") or {}
    kid = key_id or proof.get("arp_issuer_key_id") or envelope.get("arp_issuer_key_id")
    sig = proof.get("signature")
    if not kid or not sig:
        return False
    body = {k: v for k, v in envelope.items() if k != "proof"}
    expected = hmac.new(
        _arp_signing_secret(kid), _canonical(body), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


def build_portable_arp_envelope(
    *,
    arp_id: str,
    transcript_digest: str,
    digest_alg: str,
    score_payload: dict[str, Any],
    grader_version: str,
    sealed_rubric_id: str,
    transcript_rubric_schema_version: str,
    alternate_rubric_id: str,
    alternate_rubric_version: str,
    authority_id: str,
    signed_at: datetime | None = None,
    valid_until: datetime | None = None,
    key_id: str = DEFAULT_ARP_ISSUER_KEY_ID,
    regrade_id_cite: str | None = None,
    credential_id_cite: str | None = None,
) -> dict[str, Any]:
    signed_at = signed_at or datetime.now(timezone.utc)
    valid_until = valid_until or (signed_at + DEFAULT_ENVELOPE_TTL)
    claims: dict[str, Any] = {
        "type": ["NaadiAlternateRubricProof"],
        "artifact_schema_version": ARTIFACT_SCHEMA_V1,
        "arp_id": arp_id,
        "transcript_digest": transcript_digest,
        "digest_alg": digest_alg,
        "score_payload": score_payload,
        "grader_version": grader_version,
        "sealed_rubric_id": sealed_rubric_id,
        "transcript_rubric_schema_version": transcript_rubric_schema_version,
        "alternate_rubric_id": alternate_rubric_id,
        "alternate_rubric_version": alternate_rubric_version,
        "authority_id": authority_id,
        "recomputed_at": signed_at.isoformat(),
        "signed_at": signed_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "arp_issuer_key_id": key_id,
    }
    if regrade_id_cite:
        claims["regrade_id"] = regrade_id_cite
    if credential_id_cite:
        claims["credential_id"] = credential_id_cite
    assert "session_id" not in claims
    return sign_arp_claims(claims, key_id=key_id)
