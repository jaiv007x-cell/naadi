"""G.b offline credential + status-list verification (Q4 state machine)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from services.pratibimb.credentials.sign import DEFAULT_ISSUER_KEY_ID
from services.pratibimb.credentials.compat import coerce_status_list_entries_for_verify
from services.pratibimb.credentials.status_list import (
    DEFAULT_MAX_STALENESS,
    KEY_DEPRECATION_WINDOW,
    STATUS_LIST_SCHEMA_V1,
    STATUS_LIST_SCHEMA_V2,
    verify_status_list_signature,
)

log = logging.getLogger("credentials.verify")


class VerifyReject(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    retired_at: datetime | None = None


@dataclass
class VerifyResult:
    accepted: bool
    reason: str = "ok"
    warnings: tuple[str, ...] = ()


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify_credential_hmac(credential: dict[str, Any]) -> bool:
    proof = credential.get("proof") or {}
    key_id = proof.get("issuer_key_id") or credential.get("issuer_key_id") or DEFAULT_ISSUER_KEY_ID
    sig = proof.get("signature")
    if not sig:
        return False
    body = {k: v for k, v in credential.items() if k != "proof"}
    # Match credentials.sign: HMAC over canonical claims with env base secret.
    import os

    raw = os.getenv("CREDENTIAL_ISSUER_HMAC_SECRET", "dev-only-credential-issuer-secret")
    expected = hmac.new(
        raw.encode("utf-8"),
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        ),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


def offline_verify(
    credential: dict[str, Any],
    status_list: dict[str, Any],
    *,
    keyring: dict[str, KeyRecord],
    now: datetime | None = None,
    max_staleness: timedelta = DEFAULT_MAX_STALENESS,
    option_b_staleness: bool = False,
    option_b_reason: str = "",
) -> VerifyResult:
    """
    Offline verify state machine (phase_g G.b).

    Option B flags are **per-invocation only** — never sticky config.
    """
    now = now or datetime.now(timezone.utc)
    warnings: list[str] = []

    # Credential claim nonsense — before any signature check (pin 1 companion)
    c_signed = credential.get("signed_at")
    c_until = credential.get("valid_until")
    if c_signed is not None and c_until is not None:
        if _parse_dt(c_until) <= _parse_dt(c_signed):
            raise VerifyReject("credential_valid_until_le_signed_at")

    # --- status list path (a)–(f) ---
    signed_at = _parse_dt(status_list["signed_at"])
    valid_until = _parse_dt(status_list["valid_until"])

    # (a) nonsense envelope — before signature check
    if valid_until <= signed_at:
        raise VerifyReject("valid_until_le_signed_at")

    schema = status_list.get("status_list_schema_version")
    if schema not in (STATUS_LIST_SCHEMA_V1, STATUS_LIST_SCHEMA_V2, None, ""):
        raise VerifyReject("unknown_status_list_schema")

    key_id = status_list.get("key_id") or (status_list.get("proof") or {}).get("key_id")
    if not key_id or key_id not in keyring:
        raise VerifyReject("unknown_key_id")

    rec = keyring[key_id]
    if rec.retired_at is not None:
        age = now - rec.retired_at
        if age > KEY_DEPRECATION_WINDOW:
            raise VerifyReject("key_retired_beyond_window")
        # Within deprecation window: still verifies with warning (pin 2).
        warnings.append("retired_key_within_window")

    # (c) signature
    if not verify_status_list_signature(status_list):
        raise VerifyReject("status_list_bad_signature")

    # (d) hard expiry
    if now > valid_until:
        raise VerifyReject("status_list_expired")

    # (e) staleness
    if now - signed_at > max_staleness:
        if not option_b_staleness:
            raise VerifyReject("status_list_stale")
        if not option_b_reason:
            raise VerifyReject("option_b_requires_reason")
        log.log(
            logging.INFO + 10,
            "AUDIT option_b_staleness signed_at=%s reason=%s",
            signed_at.isoformat(),
            option_b_reason,
        )
        warnings.append("stale_status_list")

    # (f) revocation — v1 entries coerced credentials-side only (compat.py)
    cred_id = credential.get("credential_id")
    coerced = coerce_status_list_entries_for_verify(status_list)
    revoked_ids = {
        e.get("identifier_id") or e.get("credential_id")
        for e in coerced
        if (e.get("identifier_kind") == "credential" or e.get("credential_id"))
        and (e.get("identifier_id") or e.get("credential_id"))
    }
    if cred_id in revoked_ids:
        raise VerifyReject("credential_revoked")

    # (g) credential signature
    if not verify_credential_hmac(credential):
        raise VerifyReject("credential_bad_signature")

    return VerifyResult(accepted=True, warnings=tuple(warnings))
