"""Auth and consent startup guards — fail-closed in production."""
from __future__ import annotations

import logging
import os

from services.pratibimb.auth.startup import is_production_auth_mode

log = logging.getLogger(__name__)

DEV_AUTH_HEADER_NAMES = frozenset(
    {
        "x-subject-id",
        "x-tenant-id",
        "x-roles",
        "x-permissions",
        "x-dev-subject",
        "x-dev-tenant",
    }
)


class StartupConfigurationError(RuntimeError):
    """Refusing to boot with unsafe auth/consent configuration."""


class ConsentStartupError(StartupConfigurationError):
    """Refusing to boot with in-memory consent store in production."""


def consent_store_backend() -> str:
    return os.getenv("CONSENT_STORE", "in_memory").lower()


def dev_header_auth_enabled() -> bool:
    raw = os.getenv("DEV_HEADER_AUTH_ENABLED")
    if raw is None:
        return not is_production_auth_mode()
    return raw.lower() in {"1", "true", "yes"}


def validate_consent_startup() -> None:
    """
    Production boot guards:
      - in-memory consent store forbidden
      - dev-header auth must be explicitly disabled
    """
    if is_production_auth_mode() and consent_store_backend() == "in_memory":
        raise ConsentStartupError(
            "AUTH_MODE=production with CONSENT_STORE=in_memory is forbidden. "
            "Configure CONSENT_STORE=postgres and a durable ConsentGrantStore."
        )
    if is_production_auth_mode() and dev_header_auth_enabled():
        raise StartupConfigurationError(
            "AUTH_MODE=production with DEV_HEADER_AUTH_ENABLED is forbidden. "
            "Dev-header auth must be disabled in production."
        )
    if is_production_auth_mode():
        log.info(
            "consent store: production mode backend=%s dev_headers=disabled",
            consent_store_backend(),
        )


def find_dev_auth_headers(headers: dict[str, str]) -> list[str]:
    """Return dev-auth header names present on the request (case-insensitive)."""
    lower = {k.lower(): k for k in headers}
    return sorted(
        lower[name]
        for name in DEV_AUTH_HEADER_NAMES
        if name in lower and headers.get(lower[name])
    )
