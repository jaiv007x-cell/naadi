"""
JWT verification via tenant-scoped JWKS registry.

Registry lookup on (tenant_id, iss) runs before signature verification.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from jose import jwk as jose_jwk
from jose import jwt as jose_jwt
from jose.exceptions import ExpiredSignatureError, JWTError

from services.pratibimb.auth.claims import (
    CoarseClaims,
    DisallowedClaimError,
    MalformedClaimsError,
)
from services.pratibimb.auth.tenant_jwks import (
    IssuerRetiredError,
    TenantJwks,
    TenantResolutionError,
    enforce_retirement,
)

log = logging.getLogger(__name__)


class JwtVerificationError(RuntimeError):
    """Base JWT verification failure."""


class JwtExpiredError(JwtVerificationError):
    """Token expired."""


class JwtSignatureError(JwtVerificationError):
    """Signature or structural verification failure."""


class JwtIssuerError(JwtVerificationError):
    """Issuer mismatch or unknown tenant."""


class JwtAudienceError(JwtVerificationError):
    """Audience mismatch."""


class JwtVerifier:
    def __init__(self, tenant_jwks: TenantJwks) -> None:
        self._tenant_jwks = tenant_jwks

    def verify(self, token: str) -> CoarseClaims:
        try:
            unverified = jose_jwt.get_unverified_claims(token)
            header = jose_jwt.get_unverified_header(token)
        except JWTError as e:
            raise JwtSignatureError(f"malformed token: {e}") from e

        tenant_id = unverified.get("tenant_id")
        issuer = unverified.get("iss")
        if not tenant_id:
            raise MalformedClaimsError("token missing tenant_id claim")
        if not issuer:
            raise MalformedClaimsError("token missing iss claim")

        kid = header.get("kid")
        try:
            keys, resolved = self._tenant_jwks.keys_for(
                str(tenant_id), str(issuer), kid
            )
        except TenantResolutionError as e:
            raise JwtIssuerError(str(e)) from e

        jwk_dict = _select_key(keys, kid)
        if jwk_dict is None:
            raise JwtSignatureError(
                f"no JWKS key matches kid={kid!r} for tenant {tenant_id}"
            )

        try:
            key = jose_jwk.construct(jwk_dict)
            payload = jose_jwt.decode(
                token,
                key=key,
                algorithms=list(resolved.algorithms),
                issuer=resolved.issuer,
                options={
                    "require": ["exp", "iat", "sub", "jti"],
                    "verify_aud": False,
                },
            )
        except ExpiredSignatureError as e:
            raise JwtExpiredError(str(e)) from e
        except JWTError as e:
            msg = str(e).lower()
            if "issuer" in msg:
                raise JwtIssuerError(str(e)) from e
            raise JwtSignatureError(str(e)) from e

        aud = payload.get("aud")
        aud_values = aud if isinstance(aud, list) else [aud]
        if not any(a in resolved.audiences for a in aud_values if a):
            raise JwtAudienceError(
                f"audience {aud!r} not in allow-list for tenant {tenant_id}"
            )

        try:
            claims = CoarseClaims.from_payload(payload)
        except (MalformedClaimsError, DisallowedClaimError) as e:
            raise JwtSignatureError(str(e)) from e

        try:
            enforce_retirement(resolved, claims.iat)
        except IssuerRetiredError as e:
            raise JwtIssuerError(str(e)) from e

        now = datetime.now(timezone.utc)
        if claims.exp <= now:
            raise JwtExpiredError(f"token expired at {claims.exp.isoformat()}")
        if claims.nbf and claims.nbf > now:
            raise JwtSignatureError(
                f"token not yet valid (nbf={claims.nbf.isoformat()})"
            )

        return claims


def _select_key(keys: list[dict[str, Any]], kid: str | None) -> dict[str, Any] | None:
    if kid is None:
        return keys[0] if len(keys) == 1 else None
    for k in keys:
        if k.get("kid") == kid:
            return k
    return None
