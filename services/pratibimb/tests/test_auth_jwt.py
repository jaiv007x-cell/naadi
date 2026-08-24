"""JWT auth unit tests — coarse claims, JWKS cache, verifier, dev-header gating."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwt
from jose.utils import base64url_encode

from services.pratibimb.app.main import app
from services.pratibimb.auth.claims import (
    CoarseClaims,
    DisallowedClaimError,
    MalformedClaimsError,
)
from services.pratibimb.auth.context import JwtAuthContextProvider
from services.pratibimb.auth.jwks_cache import JwksCache, JwksFetchError
from services.pratibimb.auth.jwt_verifier import (
    JwtAudienceError,
    JwtExpiredError,
    JwtIssuerError,
    JwtSignatureError,
    JwtVerifier,
)
from services.pratibimb.auth.startup import (
    DevHeaderInProductionError,
    validate_auth_startup,
)
from services.pratibimb.ledger_read.deps import reset_auth_wiring_cache
from services.pratibimb.auth.tenant_jwks import (
    InMemoryTenantIssuerRegistry,
    ResolvedIssuer,
    TenantJwks,
)

TENANT = "tenant-virohan"
ISS = "https://idp.test/virohan"
JWKS_URL = "https://idp.test/virohan/jwks"
AUD = "pratibimb-ledger-read"
KID = "test-key-1"


def _b64url_int(num: int) -> str:
    byte_length = (num.bit_length() + 7) // 8
    return base64url_encode(num.to_bytes(byte_length, "big")).decode("ascii")


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    numbers = public_key.public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_int(numbers.n),
        "e": _b64url_int(numbers.e),
    }
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwk, private_pem


@pytest.fixture
def tenant_jwks(rsa_keypair):
    jwk, _ = rsa_keypair
    registry = InMemoryTenantIssuerRegistry(
        {
            (TENANT, ISS): ResolvedIssuer(
                tenant_id=TENANT,
                issuer=ISS,
                jwks_url=JWKS_URL,
                audiences=frozenset({AUD}),
                algorithms=frozenset({"RS256"}),
            )
        },
        frozenset({TENANT}),
    )
    cache = JwksCache()
    cache.seed(JWKS_URL, [jwk])
    return TenantJwks(registry, cache)


@pytest.fixture
def verifier(tenant_jwks):
    return JwtVerifier(tenant_jwks)


def _claims_payload(
    sub: str = "L1",
    *,
    ttl_s: int = 300,
    extra: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "tenant_id": TENANT,
        "iss": ISS,
        "aud": AUD,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_s)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    if extra:
        payload.update(extra)
    return payload


def _sign(payload: dict, private_pem: bytes) -> str:
    return jwt.encode(
        payload,
        private_pem,
        algorithm="RS256",
        headers={"kid": KID},
    )


def test_coarse_claims_rejects_scopes_claim():
    payload = _claims_payload(extra={"scopes": ["self.learner"]})
    with pytest.raises(DisallowedClaimError, match="disallowed claims"):
        CoarseClaims.from_payload(payload)


def test_coarse_claims_requires_jti():
    payload = _claims_payload()
    del payload["jti"]
    with pytest.raises(MalformedClaimsError, match="jti"):
        CoarseClaims.from_payload(payload)


def test_verifier_happy_path(verifier, rsa_keypair):
    _, private_pem = rsa_keypair
    token = _sign(_claims_payload(), private_pem)
    claims = verifier.verify(token)
    assert claims.sub == "L1"
    assert claims.tenant_id == TENANT


def test_verifier_expired_token(verifier, rsa_keypair):
    _, private_pem = rsa_keypair
    token = _sign(_claims_payload(ttl_s=-60), private_pem)
    with pytest.raises(JwtExpiredError):
        verifier.verify(token)


def test_verifier_wrong_issuer(verifier, rsa_keypair):
    _, private_pem = rsa_keypair
    payload = _claims_payload()
    payload["iss"] = "https://evil.example"
    token = _sign(payload, private_pem)
    with pytest.raises(JwtIssuerError):
        verifier.verify(token)


def test_verifier_wrong_audience(verifier, rsa_keypair):
    _, private_pem = rsa_keypair
    payload = _claims_payload()
    payload["aud"] = "other-service"
    token = _sign(payload, private_pem)
    with pytest.raises(JwtAudienceError):
        verifier.verify(token)


def test_verifier_tampered_signature(verifier, rsa_keypair):
    _, private_pem = rsa_keypair
    token = _sign(_claims_payload(), private_pem)
    tampered = token[:-4] + "XXXX"
    with pytest.raises(JwtSignatureError):
        verifier.verify(tampered)


def test_verifier_disallowed_scope_claim_in_token(verifier, rsa_keypair):
    _, private_pem = rsa_keypair
    token = _sign(_claims_payload(extra={"scopes": ["self.learner"]}), private_pem)
    with pytest.raises(JwtSignatureError):
        verifier.verify(token)


def test_jwks_fail_closed_without_cache(rsa_keypair):
    registry = InMemoryTenantIssuerRegistry(
        {
            (TENANT, ISS): ResolvedIssuer(
                tenant_id=TENANT,
                issuer=ISS,
                jwks_url=JWKS_URL,
                audiences=frozenset({AUD}),
                algorithms=frozenset({"RS256"}),
            )
        },
        frozenset({TENANT}),
    )
    cache = JwksCache(timeout_s=0.1)
    verifier = JwtVerifier(TenantJwks(registry, cache))
    _, private_pem = rsa_keypair
    token = _sign(_claims_payload(), private_pem)
    with pytest.raises(JwtIssuerError):
        verifier.verify(token)


def test_jwks_stale_grace_served(monkeypatch, rsa_keypair):
    from services.pratibimb.auth.jwks_cache import _CacheEntry

    jwk, private_pem = rsa_keypair
    registry = InMemoryTenantIssuerRegistry(
        {
            (TENANT, ISS): ResolvedIssuer(
                tenant_id=TENANT,
                issuer=ISS,
                jwks_url=JWKS_URL,
                audiences=frozenset({AUD}),
                algorithms=frozenset({"RS256"}),
            )
        },
        frozenset({TENANT}),
    )
    cache = JwksCache(ttl_s=1, stale_grace_s=60, timeout_s=0.1)
    cache._cache[JWKS_URL] = _CacheEntry(
        document={"keys": [jwk]}, fetched_at=0.0, ttl_s=1
    )
    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    verifier = JwtVerifier(TenantJwks(registry, cache))
    token = _sign(_claims_payload(), private_pem)
    claims = verifier.verify(token)
    assert claims.sub == "L1"


def test_production_refuses_auth_allow_dev_header(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "true")
    with pytest.raises(DevHeaderInProductionError):
        validate_auth_startup()


def test_dev_header_provider_only_when_allowed(verifier):
    provider = JwtAuthContextProvider(verifier, allow_dev_header=True)
    from starlette.requests import Request

    req = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/",
            "headers": [
                (b"x-dev-subject", b"L9"),
                (b"x-dev-tenant", TENANT.encode()),
            ],
        }
    )
    ctx = provider.from_request(req)
    assert ctx.subject_pseudo_id == "L9"
    assert ctx.source == "dev_header"


def test_production_rejects_dev_bypass_headers(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("CONSENT_STORE", "postgres")
    monkeypatch.setenv("DEV_HEADER_AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "false")
    client = TestClient(app)
    resp = client.get(
        "/health",
        headers={"X-Dev-Subject": "L1", "X-Dev-Tenant": TENANT},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "dev_auth_forbidden"


def test_jwt_route_rejects_token_with_embedded_scopes(monkeypatch, rsa_keypair):
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("AUTH_ALLOW_DEV_HEADER", "false")
    monkeypatch.setenv(
        "TENANT_TRUST_CONFIG",
        f"tenant={TENANT},iss={ISS},aud={AUD},jwks={JWKS_URL}",
    )
    reset_auth_wiring_cache()
    from services.pratibimb.ledger_read.deps import get_jwks_cache

    get_jwks_cache().seed(JWKS_URL, [rsa_keypair[0]])

    _, private_pem = rsa_keypair
    token = _sign(_claims_payload(extra={"scopes": ["self.learner"]}), private_pem)
    client = TestClient(app)
    resp = client.post(
        "/v1/ledger/evidence/learner",
        json={"learner_pseudo_id": "L1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_token"
    reset_auth_wiring_cache()
