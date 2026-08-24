"""H.a portable regrade envelope signing — distinct regrade-issuer key (I-H-14 option b)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_REGRADE_ISSUER_KEY_ID = "dev-regrade-issuer-key-1"
ARTIFACT_SCHEMA_V1 = "regrade_artifact.v1"
DEFAULT_ENVELOPE_TTL = timedelta(days=365)


def _regrade_signing_secret(key_id: str) -> bytes:
    """Distinct material from G credential issuer — option b (I-H-14)."""
    base = os.getenv(
        "CREDENTIAL_REGRADE_HMAC_SECRET",
        "dev-only-regrade-issuer-secret",
    )
    # Derive per-key so wrong-key tests cannot accidentally verify under G secret.
    return hmac.new(base.encode("utf-8"), key_id.encode("utf-8"), hashlib.sha256).digest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def sign_regrade_claims(
    claims: dict[str, Any],
    *,
    key_id: str = DEFAULT_REGRADE_ISSUER_KEY_ID,
) -> dict[str, Any]:
    body = {k: v for k, v in claims.items() if k != "proof"}
    sig = hmac.new(_regrade_signing_secret(key_id), _canonical(body), hashlib.sha256).hexdigest()
    return {
        **body,
        "proof": {
            "type": "JsonHmac2026",
            "regrade_issuer_key_id": key_id,
            "signature": sig,
        },
    }


def verify_regrade_signature(envelope: dict[str, Any], *, key_id: str | None = None) -> bool:
    proof = envelope.get("proof") or {}
    kid = key_id or proof.get("regrade_issuer_key_id") or envelope.get("regrade_issuer_key_id")
    sig = proof.get("signature")
    if not kid or not sig:
        return False
    body = {k: v for k, v in envelope.items() if k != "proof"}
    expected = hmac.new(
        _regrade_signing_secret(kid), _canonical(body), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


def build_portable_envelope(
    *,
    regrade_id: str,
    transcript_digest: str,
    grade_payload: dict[str, Any],
    grader_version: str,
    rubric_schema_version: str,
    signed_at: datetime | None = None,
    valid_until: datetime | None = None,
    key_id: str = DEFAULT_REGRADE_ISSUER_KEY_ID,
    digest_alg: str,
) -> dict[str, Any]:
    signed_at = signed_at or datetime.now(timezone.utc)
    valid_until = valid_until or (signed_at + DEFAULT_ENVELOPE_TTL)
    claims = {
        "type": ["NaadiRegradeArtifact"],
        "artifact_schema_version": ARTIFACT_SCHEMA_V1,
        "regrade_id": regrade_id,
        "transcript_digest": transcript_digest,
        "digest_alg": digest_alg,
        "grade_payload": grade_payload,
        "grader_version": grader_version,
        "rubric_schema_version": rubric_schema_version,
        "regraded_at": signed_at.isoformat(),
        "signed_at": signed_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "regrade_issuer_key_id": key_id,
    }
    # I-H-1: never put session_id / learner_pseudo_id on the wire.
    assert "session_id" not in claims
    return sign_regrade_claims(claims, key_id=key_id)
