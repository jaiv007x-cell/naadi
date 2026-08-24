"""
JwtAuthContextProvider — turns an incoming request into an AuthContext.

AuthContext carries identity only (from CoarseClaims). Authorization is
resolved on demand via ConsentGrantStore + ConsentResolver.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from fastapi import HTTPException, Request, status

from services.pratibimb.auth.claims import (
    DisallowedClaimError,
    MalformedClaimsError,
)
from services.pratibimb.auth.jwt_verifier import (
    JwtAudienceError,
    JwtExpiredError,
    JwtIssuerError,
    JwtSignatureError,
    JwtVerifier,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthContext:
    subject_pseudo_id: str
    tenant_id: str
    jti: str
    issued_at: datetime
    expires_at: datetime
    source: str  # "jwt" or "dev_header"


class AuthContextProvider(Protocol):
    def from_request(self, request: Request) -> AuthContext: ...


class JwtAuthContextProvider:
    """
    Production auth path. Parses Authorization: Bearer <jwt>, verifies via
    JwtVerifier. Consent resolves separately through ConsentGrantStore.
    """

    def __init__(
        self,
        verifier: JwtVerifier,
        *,
        allow_dev_header: bool = False,
    ) -> None:
        self._verifier = verifier
        self._allow_dev_header = allow_dev_header

    def from_request(self, request: Request) -> AuthContext:
        if self._allow_dev_header:
            dev = request.headers.get("x-dev-subject")
            dev_tenant = request.headers.get("x-dev-tenant")
            if dev and dev_tenant:
                log.warning(
                    "auth.dev_header_used sub=%s tenant=%s path=%s",
                    dev,
                    dev_tenant,
                    request.url.path,
                )
                now = datetime.now(timezone.utc)
                return AuthContext(
                    subject_pseudo_id=dev,
                    tenant_id=dev_tenant,
                    jti="dev-header",
                    issued_at=now,
                    expires_at=now,
                    source="dev_header",
                )

        authz = request.headers.get("authorization") or request.headers.get(
            "Authorization"
        )
        if not authz or not authz.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "missing_bearer"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = authz.split(None, 1)[1].strip()
        try:
            claims = self._verifier.verify(token)
        except JwtExpiredError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "token_expired"},
            ) from None
        except (
            JwtSignatureError,
            JwtIssuerError,
            JwtAudienceError,
            MalformedClaimsError,
            DisallowedClaimError,
        ) as e:
            log.info("auth.jwt_reject reason=%s", type(e).__name__)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "invalid_token"},
            ) from e

        return AuthContext(
            subject_pseudo_id=claims.sub,
            tenant_id=claims.tenant_id,
            jti=claims.jti,
            issued_at=claims.iat,
            expires_at=claims.exp,
            source="jwt",
        )
