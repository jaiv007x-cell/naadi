"""URL-based JWKS fetch + cache. Network policy owned here; tenant binding in tenant_jwks."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

JWKS_TTL_S = 300
JWKS_STALE_GRACE_S = 60
JWKS_HTTP_TIMEOUT_S = 3.0


class JwksUnavailableError(RuntimeError):
    """Fail-closed: JWKS could not be fetched and no usable cache exists."""


class JwksFetchError(JwksUnavailableError):
    """JWKS HTTP fetch failed and stale grace exhausted."""


@dataclass
class _CacheEntry:
    document: dict[str, Any]
    fetched_at: float
    ttl_s: int

    def is_fresh(self, now: float) -> bool:
        return (now - self.fetched_at) < self.ttl_s

    def is_within_stale_grace(self, now: float, grace_s: int) -> bool:
        return (now - self.fetched_at) < (self.ttl_s + grace_s)


class JwksCache:
    """Caches JWKS documents keyed by URL."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        ttl_s: int = JWKS_TTL_S,
        stale_grace_s: int = JWKS_STALE_GRACE_S,
        timeout_s: float = JWKS_HTTP_TIMEOUT_S,
    ) -> None:
        self._http = http_client or httpx.Client(timeout=timeout_s)
        self._ttl_s = ttl_s
        self._stale_grace_s = stale_grace_s
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, jwks_url: str) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(jwks_url)

        if entry and entry.is_fresh(now):
            return entry.document

        try:
            fresh = self._fetch(jwks_url)
        except Exception as e:
            if entry and entry.is_within_stale_grace(now, self._stale_grace_s):
                log.warning(
                    "jwks.stale_served url=%s age_s=%.1f err=%s",
                    jwks_url,
                    now - entry.fetched_at,
                    e,
                )
                return entry.document
            log.error("jwks.fail_closed url=%s err=%s", jwks_url, e)
            raise JwksFetchError(
                f"JWKS unavailable for {jwks_url!r} and no usable cache"
            ) from e

        with self._lock:
            self._cache[jwks_url] = _CacheEntry(
                document=fresh,
                fetched_at=now,
                ttl_s=self._ttl_s,
            )
        return fresh

    def seed(self, jwks_url: str, keys: list[dict[str, Any]]) -> None:
        """Test helper — preload JWKS without HTTP."""
        with self._lock:
            self._cache[jwks_url] = _CacheEntry(
                document={"keys": keys},
                fetched_at=time.monotonic(),
                ttl_s=self._ttl_s,
            )

    def _fetch(self, jwks_url: str) -> dict[str, Any]:
        resp = self._http.get(jwks_url)
        resp.raise_for_status()
        doc = resp.json()
        keys = doc.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ValueError(f"JWKS response at {jwks_url} contained no keys")
        return doc

    def invalidate(self, jwks_url: str) -> None:
        with self._lock:
            self._cache.pop(jwks_url, None)
