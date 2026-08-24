"""
Tests for tenant_jwks.py — multi-tenant JWKS resolution & JWT verification.

Covers:
  * Happy-path resolution (active tenant, active issuer, valid JWKS)
  * End-to-end JwtVerifier with real RSA signatures
  * Cross-tenant isolation (issuer bound to tenant A cannot verify for tenant B)
  * Unknown / suspended tenant → fail closed
  * Retired issuer with grace period (accept within, reject after)
  * KID pinning (unknown kid → reject)
  * URL policy (non-HTTPS, host mismatch → reject)
  * Audience allow-listing (aud not in tenant allowlist → reject)
  * JWKS cache behavior (seed hit, HTTP fetch miss, stale grace)
  * Global uniqueness of active issuers (DB-level partial unique index)
  * InMemory ↔ Sql registry parity
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import base64url_encode
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services.pratibimb.auth.jwks_cache import JwksCache, JwksFetchError, _CacheEntry
from services.pratibimb.auth.jwt_verifier import (
    JwtAudienceError,
    JwtIssuerError,
    JwtVerifier,
)
from services.pratibimb.auth.tenant_jwks import (
    InMemoryTenantIssuerRegistry,
    IssuerRetiredError,
    IssuerTenantMismatchError,
    JwksUrlPolicyError,
    ResolvedIssuer,
    SqlTenantIssuerRegistry,
    SuspendedTenantError,
    TenantJwks,
    UnknownIssuerError,
    UnknownTenantError,
    _validate_jwks_url_policy,
    enforce_retirement,
    registry_from_env,
)
from services.pratibimb.ledger.models import Base
import services.pratibimb.auth.models_registry  # noqa: F401

UTC = timezone.utc

TENANT_ALPHA = "tnt_alpha_9f2a"
TENANT_BETA = "tnt_beta_4c11"
TENANT_SUSPENDED = "tnt_suspended_7d3e"
TENANT_GHOST = "tnt_ghost_0000"

ISSUER_ALPHA = "https://idp.alpha.example.com"
ISSUER_ALPHA_LEGACY = "https://idp.alpha.example.com/legacy"
ISSUER_BETA = "https://idp.beta.example.com"

JWKS_URL_ALPHA = "https://idp.alpha.example.com/.well-known/jwks.json"
JWKS_URL_ALPHA_LEGACY = "https://idp.alpha.example.com/legacy/.well-known/jwks.json"
JWKS_URL_BETA = "https://idp.beta.example.com/.well-known/jwks.json"

AUD_API = "pratibimb-api"
AUD_LEDGER = "pratibimb-ledger"


# --------------------------------------------------------------------------- #
# Crypto fixtures — real RSA keys so signatures are actually verified.        #
# --------------------------------------------------------------------------- #


def _b64url_int(num: int) -> str:
    byte_length = (num.bit_length() + 7) // 8
    return base64url_encode(num.to_bytes(byte_length, "big")).decode("ascii")


@dataclass
class KeyPair:
    kid: str
    private_pem: bytes
    jwk: dict[str, Any]


def _make_keypair(kid: str) -> KeyPair:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_int(numbers.n),
        "e": _b64url_int(numbers.e),
    }
    return KeyPair(kid=kid, private_pem=pem, jwk=jwk)


@pytest.fixture(scope="module")
def kp_alpha_v1() -> KeyPair:
    return _make_keypair("alpha-key-1")


@pytest.fixture(scope="module")
def kp_alpha_v2() -> KeyPair:
    return _make_keypair("alpha-key-2")


@pytest.fixture(scope="module")
def kp_beta_v1() -> KeyPair:
    return _make_keypair("beta-key-1")


# --------------------------------------------------------------------------- #
# Registry + JWKS HTTP mock                                                   #
# --------------------------------------------------------------------------- #


class FakeJwksHttp:
    """Routes JWKS URLs to key documents; counts fetch attempts."""

    def __init__(self) -> None:
        self._routes: dict[str, list[dict[str, Any]]] = {}
        self.call_count: dict[str, int] = {}

    def set(self, url: str, keys: list[KeyPair]) -> None:
        self._routes[url] = [k.jwk for k in keys]

    def fetch(self, url: str) -> dict[str, Any]:
        self.call_count[url] = self.call_count.get(url, 0) + 1
        if url not in self._routes:
            raise ConnectionError(f"no route for {url}")
        return {"keys": list(self._routes[url])}


def _mk_resolved(
    *,
    tenant_id: str = TENANT_ALPHA,
    issuer: str = ISSUER_ALPHA,
    jwks_url: str = JWKS_URL_ALPHA,
    audiences: frozenset[str] | None = None,
    algorithms: frozenset[str] | None = None,
    retired_at: datetime | None = None,
    retirement_grace_s: int = 0,
    kid_pin: frozenset[str] | None = None,
    jwks_host_override: str | None = None,
) -> ResolvedIssuer:
    return ResolvedIssuer(
        tenant_id=tenant_id,
        issuer=issuer,
        jwks_url=jwks_url,
        audiences=audiences or frozenset({AUD_API, AUD_LEDGER}),
        algorithms=algorithms or frozenset({"RS256"}),
        retired_at=retired_at,
        retirement_grace_s=retirement_grace_s,
        kid_pin=kid_pin,
        jwks_host_override=jwks_host_override,
    )


def _mk_bindings(
    *,
    legacy_retired_at: datetime | None = None,
    legacy_grace_s: int = 0,
) -> dict[tuple[str, str], ResolvedIssuer]:
    return {
        (TENANT_ALPHA, ISSUER_ALPHA): _mk_resolved(),
        (TENANT_ALPHA, ISSUER_ALPHA_LEGACY): _mk_resolved(
            issuer=ISSUER_ALPHA_LEGACY,
            jwks_url=JWKS_URL_ALPHA_LEGACY,
            retired_at=legacy_retired_at,
            retirement_grace_s=legacy_grace_s,
        ),
        (TENANT_BETA, ISSUER_BETA): _mk_resolved(
            tenant_id=TENANT_BETA,
            issuer=ISSUER_BETA,
            jwks_url=JWKS_URL_BETA,
            audiences=frozenset({AUD_API}),
        ),
    }


@pytest.fixture
def registry() -> InMemoryTenantIssuerRegistry:
    retired = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    bindings = _mk_bindings(
        legacy_retired_at=retired,
        legacy_grace_s=int(timedelta(days=30).total_seconds()),
    )
    return InMemoryTenantIssuerRegistry(
        bindings,
        frozenset({TENANT_ALPHA, TENANT_BETA}),
    )


@pytest.fixture
def http(kp_alpha_v1: KeyPair, kp_beta_v1: KeyPair) -> FakeJwksHttp:
    fake = FakeJwksHttp()
    fake.set(JWKS_URL_ALPHA, [kp_alpha_v1])
    fake.set(JWKS_URL_ALPHA_LEGACY, [kp_alpha_v1])
    fake.set(JWKS_URL_BETA, [kp_beta_v1])
    return fake


@pytest.fixture
def cache(http: FakeJwksHttp) -> JwksCache:
    c = JwksCache(ttl_s=300, stale_grace_s=60, timeout_s=1.0)
    c._fetch = http.fetch  # type: ignore[method-assign]
    return c


@pytest.fixture
def tenant_jwks(registry: InMemoryTenantIssuerRegistry, cache: JwksCache) -> TenantJwks:
    return TenantJwks(registry, cache)


@pytest.fixture
def verifier(tenant_jwks: TenantJwks) -> JwtVerifier:
    return JwtVerifier(tenant_jwks)


def _mint(
    kp: KeyPair,
    *,
    iss: str,
    aud: str | list[str],
    tenant_id: str,
    sub: str = "user_1a2b",
    exp_delta: int = 300,
    iat: datetime | None = None,
    exp: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    now = iat or datetime.now(UTC)
    expires = exp or (now + timedelta(seconds=exp_delta))
    payload: dict[str, Any] = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "tenant_id": tenant_id,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(
        payload,
        kp.private_pem,
        algorithm="RS256",
        headers={"kid": kp.kid},
    )


# =========================================================================== #
# 1. Happy path                                                               #
# =========================================================================== #


class TestHappyPath:
    def test_active_tenant_active_issuer_valid_token(
        self, verifier: JwtVerifier, kp_alpha_v1: KeyPair
    ):
        token = _mint(
            kp_alpha_v1,
            iss=ISSUER_ALPHA,
            aud=AUD_API,
            tenant_id=TENANT_ALPHA,
        )
        claims = verifier.verify(token)
        assert claims.sub == "user_1a2b"
        assert claims.tenant_id == TENANT_ALPHA

    def test_multi_audience_token_when_one_is_allowed(
        self, verifier: JwtVerifier, kp_alpha_v1: KeyPair
    ):
        token = _mint(
            kp_alpha_v1,
            iss=ISSUER_ALPHA,
            aud=["some-other-svc", AUD_LEDGER],
            tenant_id=TENANT_ALPHA,
        )
        claims = verifier.verify(token)
        assert claims.tenant_id == TENANT_ALPHA

    def test_resolved_issuer_shape(self, tenant_jwks: TenantJwks):
        resolved = tenant_jwks.resolve(TENANT_ALPHA, ISSUER_ALPHA)
        assert isinstance(resolved, ResolvedIssuer)
        assert resolved.tenant_id == TENANT_ALPHA
        assert resolved.issuer == ISSUER_ALPHA
        assert resolved.jwks_url == JWKS_URL_ALPHA
        assert AUD_LEDGER in resolved.audiences


# =========================================================================== #
# 2. Cross-tenant isolation                                                   #
# =========================================================================== #


class TestCrossTenantIsolation:
    def test_alpha_issuer_rejected_when_presented_for_beta(
        self, verifier: JwtVerifier, kp_alpha_v1: KeyPair
    ):
        token = _mint(
            kp_alpha_v1,
            iss=ISSUER_ALPHA,
            aud=AUD_API,
            tenant_id=TENANT_BETA,
        )
        with pytest.raises(JwtIssuerError):
            verifier.verify(token)

    def test_beta_issuer_rejected_when_presented_for_alpha(
        self, verifier: JwtVerifier, kp_beta_v1: KeyPair
    ):
        token = _mint(
            kp_beta_v1,
            iss=ISSUER_BETA,
            aud=AUD_API,
            tenant_id=TENANT_ALPHA,
        )
        with pytest.raises(JwtIssuerError):
            verifier.verify(token)

    def test_registry_cross_tenant_raises_distinct_error(self, registry):
        with pytest.raises(IssuerTenantMismatchError):
            registry.resolve(TENANT_BETA, ISSUER_ALPHA)

    def test_cross_tenant_error_is_not_unknown_issuer(self, registry):
        with pytest.raises(IssuerTenantMismatchError) as exc:
            registry.resolve(TENANT_BETA, ISSUER_ALPHA)
        assert not isinstance(exc.value, UnknownIssuerError)

    def test_forged_iss_with_wrong_signing_key(
        self, verifier: JwtVerifier, kp_alpha_v1: KeyPair
    ):
        token = _mint(
            kp_alpha_v1,
            iss=ISSUER_BETA,
            aud=AUD_API,
            tenant_id=TENANT_BETA,
        )
        with pytest.raises(JwtIssuerError):
            verifier.verify(token)


# =========================================================================== #
# 3. Unknown / suspended tenant                                               #
# =========================================================================== #


class TestTenantLifecycle:
    def test_unknown_tenant_fail_closed(
        self, verifier: JwtVerifier, kp_alpha_v1: KeyPair
    ):
        token = _mint(
            kp_alpha_v1,
            iss=ISSUER_ALPHA,
            aud=AUD_API,
            tenant_id=TENANT_GHOST,
        )
        with pytest.raises(JwtIssuerError):
            verifier.verify(token)

    def test_unknown_tenant_registry_direct(self, registry):
        with pytest.raises(UnknownTenantError):
            registry.resolve(TENANT_GHOST, ISSUER_ALPHA)

    def test_suspended_tenant_sql_fail_closed(self, pg_session):
        _seed_tenant(pg_session, TENANT_SUSPENDED, status="suspended")
        reg = SqlTenantIssuerRegistry(pg_session)
        with pytest.raises(SuspendedTenantError):
            reg.resolve(TENANT_SUSPENDED, ISSUER_ALPHA)

    def test_unknown_issuer_for_known_tenant(
        self, verifier: JwtVerifier, kp_alpha_v1: KeyPair
    ):
        token = _mint(
            kp_alpha_v1,
            iss="https://evil.example.com",
            aud=AUD_API,
            tenant_id=TENANT_ALPHA,
        )
        with pytest.raises(JwtIssuerError):
            verifier.verify(token)


# =========================================================================== #
# 4. Retirement / grace period                                                #
# =========================================================================== #


class TestRetirement:
    def test_retired_issuer_accepted_within_grace(
        self, registry: InMemoryTenantIssuerRegistry, cache: JwksCache, kp_alpha_v1: KeyPair
    ):
        now = datetime.now(UTC)
        retired = now - timedelta(days=5)
        bindings = _mk_bindings(
            legacy_retired_at=retired,
            legacy_grace_s=int(timedelta(days=30).total_seconds()),
        )
        reg = InMemoryTenantIssuerRegistry(bindings, frozenset({TENANT_ALPHA, TENANT_BETA}))
        cache.seed(JWKS_URL_ALPHA_LEGACY, [kp_alpha_v1.jwk])
        v = JwtVerifier(TenantJwks(reg, cache))
        token_iat = retired - timedelta(hours=1)
        token = _mint(
            kp_alpha_v1,
            iss=ISSUER_ALPHA_LEGACY,
            aud=AUD_API,
            tenant_id=TENANT_ALPHA,
            iat=token_iat,
            exp=now + timedelta(minutes=5),
        )
        claims = v.verify(token)
        assert claims.tenant_id == TENANT_ALPHA

    def test_retired_issuer_rejected_after_grace(
        self, kp_alpha_v1: KeyPair, cache: JwksCache
    ):
        now = datetime.now(UTC)
        retired = now - timedelta(days=10)
        resolved = _mk_resolved(
            issuer=ISSUER_ALPHA_LEGACY,
            jwks_url=JWKS_URL_ALPHA_LEGACY,
            retired_at=retired,
            retirement_grace_s=3600,
        )
        reg = InMemoryTenantIssuerRegistry(
            {(TENANT_ALPHA, ISSUER_ALPHA_LEGACY): resolved},
            frozenset({TENANT_ALPHA}),
        )
        cache.seed(JWKS_URL_ALPHA_LEGACY, [kp_alpha_v1.jwk])
        v = JwtVerifier(TenantJwks(reg, cache))
        token = _mint(
            kp_alpha_v1,
            iss=ISSUER_ALPHA_LEGACY,
            aud=AUD_API,
            tenant_id=TENANT_ALPHA,
            iat=retired - timedelta(hours=1),
            exp=now + timedelta(minutes=5),
        )
        with pytest.raises(JwtIssuerError):
            v.verify(token)

    def test_retired_issuer_iat_after_retirement_refuses(self):
        retired = datetime(2026, 8, 1, tzinfo=UTC)
        resolved = _mk_resolved(
            retired_at=retired,
            retirement_grace_s=int(timedelta(days=365).total_seconds()),
        )
        with pytest.raises(IssuerRetiredError):
            enforce_retirement(
                resolved,
                token_iat=retired + timedelta(seconds=1),
                now=retired + timedelta(hours=1),
            )

    def test_enforce_retirement_active_issuer_no_raise(self):
        enforce_retirement(_mk_resolved(), token_iat=datetime.now(UTC))

    def test_enforce_retirement_within_grace_no_raise(self):
        retired = datetime(2026, 8, 1, tzinfo=UTC)
        resolved = _mk_resolved(
            retired_at=retired,
            retirement_grace_s=int(timedelta(days=30).total_seconds()),
        )
        enforce_retirement(
            resolved,
            token_iat=retired - timedelta(hours=1),
            now=retired + timedelta(days=10),
        )


# =========================================================================== #
# 5. KID pinning & JWKS key lookup                                              #
# =========================================================================== #


class TestKidPinning:
    def test_unknown_kid_rejected(
        self, verifier: JwtVerifier, kp_alpha_v2: KeyPair
    ):
        token = _mint(
            kp_alpha_v2,
            iss=ISSUER_ALPHA,
            aud=AUD_API,
            tenant_id=TENANT_ALPHA,
        )
        with pytest.raises(JwtIssuerError):
            verifier.verify(token)

    def test_kid_pin_rejects_unpinned_kid(self, cache: JwksCache, kp_alpha_v1: KeyPair):
        resolved = _mk_resolved(kid_pin=frozenset({"alpha-key-1"}))
        reg = InMemoryTenantIssuerRegistry(
            {(TENANT_ALPHA, ISSUER_ALPHA): resolved},
            frozenset({TENANT_ALPHA}),
        )
        cache.seed(JWKS_URL_ALPHA, [kp_alpha_v1.jwk])
        tj = TenantJwks(reg, cache)
        with pytest.raises(UnknownIssuerError):
            tj.keys_for(TENANT_ALPHA, ISSUER_ALPHA, "alpha-key-2")

    def test_kid_pin_accepts_pinned_kid(self, cache: JwksCache, kp_alpha_v1: KeyPair):
        resolved = _mk_resolved(kid_pin=frozenset({"alpha-key-1"}))
        reg = InMemoryTenantIssuerRegistry(
            {(TENANT_ALPHA, ISSUER_ALPHA): resolved},
            frozenset({TENANT_ALPHA}),
        )
        cache.seed(JWKS_URL_ALPHA, [kp_alpha_v1.jwk])
        keys, r = TenantJwks(reg, cache).keys_for(
            TENANT_ALPHA, ISSUER_ALPHA, "alpha-key-1"
        )
        assert len(keys) == 1
        assert r.tenant_id == TENANT_ALPHA

    def test_rotation_requires_cache_refresh(
        self, registry: InMemoryTenantIssuerRegistry, cache: JwksCache,
        kp_alpha_v1: KeyPair, kp_alpha_v2: KeyPair,
    ):
        cache.seed(JWKS_URL_ALPHA, [kp_alpha_v1.jwk])
        v = JwtVerifier(TenantJwks(registry, cache))
        t1 = _mint(kp_alpha_v1, iss=ISSUER_ALPHA, aud=AUD_API, tenant_id=TENANT_ALPHA)
        v.verify(t1)

        t2 = _mint(kp_alpha_v2, iss=ISSUER_ALPHA, aud=AUD_API, tenant_id=TENANT_ALPHA)
        with pytest.raises(JwtIssuerError):
            v.verify(t2)

        cache.seed(JWKS_URL_ALPHA, [kp_alpha_v1.jwk, kp_alpha_v2.jwk])
        claims = v.verify(t2)
        assert claims.sub == "user_1a2b"

    def test_cache_hit_no_refetch(
        self, registry: InMemoryTenantIssuerRegistry, http: FakeJwksHttp,
        kp_alpha_v1: KeyPair,
    ):
        cache = JwksCache(ttl_s=300)
        cache._fetch = http.fetch  # type: ignore[method-assign]
        v = JwtVerifier(TenantJwks(registry, cache))
        token = _mint(kp_alpha_v1, iss=ISSUER_ALPHA, aud=AUD_API, tenant_id=TENANT_ALPHA)
        v.verify(token)
        first = http.call_count.get(JWKS_URL_ALPHA, 0)
        v.verify(token)
        v.verify(token)
        assert http.call_count.get(JWKS_URL_ALPHA, 0) == first


# =========================================================================== #
# 6. URL policy                                                               #
# =========================================================================== #


class TestJwksUrlPolicy:
    def test_https_required(self):
        with pytest.raises(JwksUrlPolicyError):
            _validate_jwks_url_policy(
                "https://idp.example.com",
                "http://idp.example.com/.well-known/jwks.json",
                None,
            )

    def test_host_required(self):
        with pytest.raises(JwksUrlPolicyError):
            _validate_jwks_url_policy(
                "https://idp.example.com",
                "https:///jwks",
                None,
            )

    def test_host_must_match_issuer(self):
        with pytest.raises(JwksUrlPolicyError):
            _validate_jwks_url_policy(
                "https://idp.example.com",
                "https://evil.example.com/.well-known/jwks.json",
                None,
            )

    def test_host_override_permits_cdn(self):
        _validate_jwks_url_policy(
            "https://idp.example.com",
            "https://keys.cdn.example.net/apollo/jwks.json",
            "keys.cdn.example.net",
        )

    def test_registry_rejects_bad_jwks_url_at_resolve(self):
        bad = _mk_resolved(
            jwks_url="http://idp.alpha.example.com/.well-known/jwks.json",
        )
        reg = InMemoryTenantIssuerRegistry(
            {(TENANT_ALPHA, ISSUER_ALPHA): bad},
            frozenset({TENANT_ALPHA}),
        )
        with pytest.raises(JwksUrlPolicyError):
            reg.resolve(TENANT_ALPHA, ISSUER_ALPHA)


# =========================================================================== #
# 7. Audience allow-list                                                      #
# =========================================================================== #


class TestAudienceAllowList:
    def test_audience_not_in_allowlist_rejected(
        self, verifier: JwtVerifier, kp_alpha_v1: KeyPair
    ):
        token = _mint(
            kp_alpha_v1,
            iss=ISSUER_ALPHA,
            aud="totally-unknown-audience",
            tenant_id=TENANT_ALPHA,
        )
        with pytest.raises(JwtAudienceError):
            verifier.verify(token)

    def test_beta_tenant_rejects_ledger_audience(
        self, registry: InMemoryTenantIssuerRegistry, cache: JwksCache, kp_beta_v1: KeyPair
    ):
        cache.seed(JWKS_URL_BETA, [kp_beta_v1.jwk])
        v = JwtVerifier(TenantJwks(registry, cache))
        token = _mint(
            kp_beta_v1,
            iss=ISSUER_BETA,
            aud=AUD_LEDGER,
            tenant_id=TENANT_BETA,
        )
        with pytest.raises(JwtAudienceError):
            v.verify(token)


# =========================================================================== #
# 8. JWKS cache behavior                                                      #
# =========================================================================== #


class TestJwksCache:
    def test_seed_served_without_http(self, kp_alpha_v1: KeyPair):
        cache = JwksCache()
        cache.seed(JWKS_URL_ALPHA, [kp_alpha_v1.jwk])
        doc = cache.get(JWKS_URL_ALPHA)
        assert doc["keys"][0]["kid"] == "alpha-key-1"

    def test_fetch_failure_fails_closed(self, registry: InMemoryTenantIssuerRegistry):
        cache = MagicMock()
        cache.get.side_effect = JwksFetchError("network down")
        tj = TenantJwks(registry, cache)
        with pytest.raises(UnknownIssuerError):
            tj.keys_for(TENANT_ALPHA, ISSUER_ALPHA, "alpha-key-1")

    def test_stale_grace_served_on_fetch_failure(
        self, monkeypatch, registry: InMemoryTenantIssuerRegistry, kp_alpha_v1: KeyPair
    ):
        cache = JwksCache(ttl_s=1, stale_grace_s=60, timeout_s=0.1)
        cache._cache[JWKS_URL_ALPHA] = _CacheEntry(
            document={"keys": [kp_alpha_v1.jwk]},
            fetched_at=0.0,
            ttl_s=1,
        )
        cache._fetch = MagicMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
        monkeypatch.setattr(time, "monotonic", lambda: 10.0)
        keys, _ = TenantJwks(registry, cache).keys_for(
            TENANT_ALPHA, ISSUER_ALPHA, "alpha-key-1"
        )
        assert keys[0]["kid"] == "alpha-key-1"

    def test_algorithm_filter_drops_disallowed(self):
        resolved = _mk_resolved(algorithms=frozenset({"RS256"}))
        reg = MagicMock()
        reg.resolve.return_value = resolved
        cache = MagicMock()
        cache.get.return_value = {
            "keys": [
                {"kid": "k1", "alg": "RS256"},
                {"kid": "k1", "alg": "HS256"},
            ]
        }
        keys, _ = TenantJwks(reg, cache).keys_for(TENANT_ALPHA, ISSUER_ALPHA, "k1")
        assert all(k.get("alg") in {"RS256", None} for k in keys)
        assert not any(k.get("alg") == "HS256" for k in keys)


# =========================================================================== #
# 9. SQL registry + DB constraints                                            #
# =========================================================================== #


@pytest.fixture
def pg_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        yield session


def _seed_tenant(session, tenant_id: str, *, status: str = "active") -> None:
    from services.pratibimb.auth.models_registry import TenantRow

    session.add(
        TenantRow(
            tenant_id=tenant_id,
            display_name=tenant_id,
            status=status,
        )
    )
    session.commit()


def _seed_issuer(
    session,
    *,
    tenant_id: str,
    issuer: str,
    jwks_url: str,
    audiences: list[str] | None = None,
    algorithms: list[str] | None = None,
    retired_at: datetime | None = None,
    kid_pin: list[str] | None = None,
) -> None:
    from services.pratibimb.auth.models_registry import TenantIssuerRow

    session.add(
        TenantIssuerRow(
            tenant_id=tenant_id,
            issuer=issuer,
            jwks_url=jwks_url,
            audiences=audiences or [AUD_LEDGER],
            algorithms=algorithms or ["RS256"],
            retired_at=retired_at,
            kid_pin=kid_pin,
        )
    )
    session.commit()


class TestSqlRegistry:
    def test_cross_tenant_blocked(self, pg_session):
        _seed_tenant(pg_session, TENANT_ALPHA)
        _seed_tenant(pg_session, TENANT_BETA)
        _seed_issuer(
            pg_session,
            tenant_id=TENANT_ALPHA,
            issuer=ISSUER_ALPHA,
            jwks_url=JWKS_URL_ALPHA,
        )
        reg = SqlTenantIssuerRegistry(pg_session)
        with pytest.raises(IssuerTenantMismatchError):
            reg.resolve(TENANT_BETA, ISSUER_ALPHA)

    def test_parity_with_in_memory(self, pg_session):
        _seed_tenant(pg_session, TENANT_ALPHA)
        _seed_issuer(
            pg_session,
            tenant_id=TENANT_ALPHA,
            issuer=ISSUER_ALPHA,
            jwks_url=JWKS_URL_ALPHA,
            audiences=[AUD_API, AUD_LEDGER],
        )
        mem = InMemoryTenantIssuerRegistry(_mk_bindings(), frozenset({TENANT_ALPHA, TENANT_BETA}))
        sql = SqlTenantIssuerRegistry(pg_session)

        r_sql = sql.resolve(TENANT_ALPHA, ISSUER_ALPHA)
        r_mem = mem.resolve(TENANT_ALPHA, ISSUER_ALPHA)

        assert r_sql.tenant_id == r_mem.tenant_id
        assert r_sql.issuer == r_mem.issuer
        assert r_sql.jwks_url == r_mem.jwks_url
        assert r_sql.audiences == r_mem.audiences
        assert r_sql.algorithms == r_mem.algorithms

    def test_active_issuer_unique_constraint(self, pg_session):
        shared = "https://idp.shared.example.com"
        shared_jwks = "https://idp.shared.example.com/.well-known/jwks.json"
        _seed_tenant(pg_session, TENANT_ALPHA)
        _seed_tenant(pg_session, TENANT_BETA)
        _seed_issuer(
            pg_session,
            tenant_id=TENANT_ALPHA,
            issuer=shared,
            jwks_url=shared_jwks,
        )
        with pytest.raises(IntegrityError):
            _seed_issuer(
                pg_session,
                tenant_id=TENANT_BETA,
                issuer=shared,
                jwks_url=shared_jwks,
            )

    def test_retired_issuer_allows_rebind(self, pg_session):
        shared = "https://idp.shared.example.com"
        shared_jwks = "https://idp.shared.example.com/.well-known/jwks.json"
        retired_at = datetime(2026, 1, 1, tzinfo=UTC)
        _seed_tenant(pg_session, TENANT_ALPHA)
        _seed_tenant(pg_session, TENANT_BETA)
        _seed_issuer(
            pg_session,
            tenant_id=TENANT_ALPHA,
            issuer=shared,
            jwks_url=shared_jwks,
            retired_at=retired_at,
        )
        _seed_issuer(
            pg_session,
            tenant_id=TENANT_BETA,
            issuer=shared,
            jwks_url=shared_jwks,
        )
        reg = SqlTenantIssuerRegistry(pg_session)
        r = reg.resolve(TENANT_BETA, shared)
        assert r.tenant_id == TENANT_BETA


# =========================================================================== #
# 10. registry_from_env                                                       #
# =========================================================================== #


class TestRegistryFromEnv:
    def test_parses_tenant_trust_config(self, monkeypatch):
        monkeypatch.setenv(
            "TENANT_TRUST_CONFIG",
            f"tenant={TENANT_ALPHA},iss={ISSUER_ALPHA},aud={AUD_API}|{AUD_LEDGER},jwks={JWKS_URL_ALPHA}",
        )
        reg = registry_from_env()
        resolved = reg.resolve(TENANT_ALPHA, ISSUER_ALPHA)
        assert resolved.jwks_url == JWKS_URL_ALPHA
        assert AUD_LEDGER in resolved.audiences
