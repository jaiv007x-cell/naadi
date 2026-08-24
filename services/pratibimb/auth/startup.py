"""
Production boot guards for the auth path.

Refuses to boot if AUTH_MODE=production AND dev-header bypass is enabled.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


class DevHeaderInProductionError(RuntimeError):
    """Fail-closed: dev header path must never be reachable in production."""


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def is_production_auth_mode() -> bool:
    return os.environ.get("AUTH_MODE", "development").strip().lower() == "production"


def validate_auth_startup() -> None:
    mode = os.environ.get("AUTH_MODE", "development").strip().lower()
    dev_header_enabled = _bool_env("AUTH_ALLOW_DEV_HEADER")

    if mode == "production" and dev_header_enabled:
        raise DevHeaderInProductionError(
            "AUTH_MODE=production is incompatible with AUTH_ALLOW_DEV_HEADER=1. "
            "The dev-header bypass must be disabled before production boot."
        )

    if dev_header_enabled and mode != "production":
        log.warning(
            "auth.dev_header_enabled mode=%s — x-dev-subject/x-dev-tenant headers "
            "will be honored. NEVER enable this in production.",
            mode,
        )


def dev_header_allowed() -> bool:
    return (
        os.environ.get("AUTH_MODE", "development").strip().lower() != "production"
        and _bool_env("AUTH_ALLOW_DEV_HEADER")
    )
