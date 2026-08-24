"""
Coarse JWT claims only. No scopes, no roles, no cohort predicates.
The token asserts identity + tenant. The ConsentGrantStore is the authority
for what that identity is allowed to do.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


ALLOWED_CLAIMS = frozenset({
    "sub",
    "tenant_id",
    "iss",
    "aud",
    "exp",
    "iat",
    "jti",
    "nbf",
    "kid",
})

REQUIRED_CLAIMS = frozenset({
    "sub",
    "tenant_id",
    "iss",
    "aud",
    "exp",
    "iat",
    "jti",
})


@dataclass(frozen=True)
class CoarseClaims:
    sub: str
    tenant_id: str
    iss: str
    aud: str
    exp: datetime
    iat: datetime
    jti: str
    nbf: datetime | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CoarseClaims:
        extra = set(payload.keys()) - ALLOWED_CLAIMS
        if extra:
            raise DisallowedClaimError(
                f"JWT contains disallowed claims: {sorted(extra)}. "
                "Slice 2 requires coarse claims only; authorization "
                "resolves through ConsentGrantStore."
            )
        missing = REQUIRED_CLAIMS - payload.keys()
        if missing:
            raise MalformedClaimsError(
                f"JWT missing required claims: {sorted(missing)}"
            )

        return cls(
            sub=str(payload["sub"]),
            tenant_id=str(payload["tenant_id"]),
            iss=str(payload["iss"]),
            aud=str(payload["aud"]),
            exp=_to_dt(payload["exp"]),
            iat=_to_dt(payload["iat"]),
            jti=str(payload["jti"]),
            nbf=_to_dt(payload["nbf"]) if "nbf" in payload else None,
        )


def _to_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(int(v), tz=timezone.utc)


class MalformedClaimsError(ValueError):
    """JWT payload missing or invalid required claims."""


class DisallowedClaimError(ValueError):
    """JWT payload contains claims outside the coarse allowlist."""
