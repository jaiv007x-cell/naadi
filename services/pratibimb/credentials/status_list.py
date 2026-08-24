"""G.b: signed status-list envelope (pins 1–3)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

STATUS_LIST_SCHEMA_V1 = "status_list.v1"
STATUS_LIST_SCHEMA_V2 = "status_list.v2"
DEFAULT_LIST_TTL = timedelta(days=30)
DEFAULT_MAX_STALENESS = timedelta(days=7)
# Placeholder for key-retirement window (numeric N still TBD).
# TBD — do NOT hardcode a "final" production value without ops pin.
# Decision criteria (locked at G.b countersign):
#   N >= 2 × max_expected_trust_root_staleness_for_offline_verifiers
# Expect ~3 months if trust-root sync is monthly; ~6 if sync cadence unknown.
# Override via CREDENTIAL_KEY_DEPRECATION_DAYS only for local/dev experiments —
# production pin lands with trust-root staleness policy (Phase H prelude / ops).
def _key_deprecation_window() -> timedelta:
    raw = os.getenv("CREDENTIAL_KEY_DEPRECATION_DAYS")
    if raw is None:
        return timedelta(days=90)  # provisional placeholder — criteria above
    return timedelta(days=int(raw))


KEY_DEPRECATION_WINDOW = _key_deprecation_window()


def _secret_for_key(key_id: str) -> bytes:
    base = os.getenv("CREDENTIAL_ISSUER_HMAC_SECRET", "dev-only-credential-issuer-secret")
    # Per-key material derived so rotation tests can use distinct key_ids.
    return hmac.new(base.encode("utf-8"), key_id.encode("utf-8"), hashlib.sha256).digest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def sign_status_list_payload(
    payload: dict[str, Any],
    *,
    key_id: str,
) -> dict[str, Any]:
    body = {k: v for k, v in payload.items() if k != "proof"}
    sig = hmac.new(_secret_for_key(key_id), _canonical(body), hashlib.sha256).hexdigest()
    return {**body, "proof": {"type": "JsonHmac2026", "key_id": key_id, "signature": sig}}


def verify_status_list_signature(envelope: dict[str, Any]) -> bool:
    proof = envelope.get("proof") or {}
    key_id = proof.get("key_id") or envelope.get("key_id")
    if not key_id or not proof.get("signature"):
        return False
    body = {k: v for k, v in envelope.items() if k != "proof"}
    expected = hmac.new(
        _secret_for_key(key_id), _canonical(body), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, proof["signature"])


def build_status_list_envelope(
    *,
    entries: list[dict[str, Any]],
    key_id: str,
    signed_at: datetime,
    valid_until: datetime,
    schema_version: str = STATUS_LIST_SCHEMA_V1,
) -> dict[str, Any]:
    if schema_version == STATUS_LIST_SCHEMA_V2:
        clean = []
        for e in entries:
            kind = e.get("identifier_kind")
            eid = e.get("identifier_id") or e.get("credential_id")
            if not kind or not eid:
                raise ValueError("status_list.v2 requires identifier_kind + identifier_id")
            row = {"identifier_kind": kind, "identifier_id": eid}
            if e.get("revoked_at"):
                row["revoked_at"] = e["revoked_at"]
            clean.append(row)
    else:
        # v1: credential_id only in entries (minimization).
        clean = [
            {
                "credential_id": e["credential_id"],
                **({"revoked_at": e["revoked_at"]} if e.get("revoked_at") else {}),
            }
            for e in entries
        ]
    payload = {
        "status_list_schema_version": schema_version,
        "key_id": key_id,
        "signed_at": signed_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "entries": clean,
    }
    return sign_status_list_payload(payload, key_id=key_id)


def normalize_status_list_entries(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """v1 missing-kind → credential for compat window; emit coercion counter.

    Compat closes when v1 readers are retired (version-based — pin 8).
    """
    from services.pratibimb.audit.metrics import STATUS_LIST_COMPAT_COERCION_TOTAL

    out: list[dict[str, Any]] = []
    for e in envelope.get("entries") or []:
        if e.get("identifier_kind") and e.get("identifier_id"):
            out.append(dict(e))
            continue
        if e.get("credential_id"):
            STATUS_LIST_COMPAT_COERCION_TOTAL.labels(
                from_kind="missing",
                to_kind="credential",
            ).inc()
            row = {
                "identifier_kind": "credential",
                "identifier_id": e["credential_id"],
            }
            if e.get("revoked_at"):
                row["revoked_at"] = e["revoked_at"]
            out.append(row)
            continue
        out.append(dict(e))
    return out


def new_snapshot_id() -> str:
    return str(uuid.uuid4())
