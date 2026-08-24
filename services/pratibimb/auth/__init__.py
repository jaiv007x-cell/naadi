"""JWT authentication — coarse claims only; consent resolves via ConsentGrantStore."""

from services.pratibimb.auth.claims import (
    CoarseClaims,
    DisallowedClaimError,
    MalformedClaimsError,
)
from services.pratibimb.auth.context import AuthContext, JwtAuthContextProvider
from services.pratibimb.auth.jwks_cache import JwksCache, JwksFetchError, JwksUnavailableError
from services.pratibimb.auth.jwt_verifier import JwtVerifier
from services.pratibimb.auth.startup import DevHeaderInProductionError, validate_auth_startup

__all__ = [
    "AuthContext",
    "CoarseClaims",
    "DevHeaderInProductionError",
    "DisallowedClaimError",
    "JwksCache",
    "JwksFetchError",
    "JwksUnavailableError",
    "JwtAuthContextProvider",
    "JwtVerifier",
    "MalformedClaimsError",
    "validate_auth_startup",
]
