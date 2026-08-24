"""MVP issuer signing — HMAC-SHA256 over canonical credential claims (G.a)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

DEFAULT_ISSUER_KEY_ID = "dev-issuer-key-1"


def _signing_secret() -> bytes:
    raw = os.getenv("CREDENTIAL_ISSUER_HMAC_SECRET", "dev-only-credential-issuer-secret")
    return raw.encode("utf-8")


def sign_credential_claims(claims: dict[str, Any], *, key_id: str = DEFAULT_ISSUER_KEY_ID) -> dict[str, Any]:
    body = json.dumps(claims, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    sig = hmac.new(_signing_secret(), body, hashlib.sha256).hexdigest()
    return {
        **claims,
        "proof": {
            "type": "JsonHmac2026",
            "issuer_key_id": key_id,
            "signature": sig,
        },
    }
