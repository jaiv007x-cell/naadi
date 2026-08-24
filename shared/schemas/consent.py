"""Consent grant schemas for ledger read authorization."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from shared.schemas.ledger_read import ConsentScope


class ConsentGrantSource(str, Enum):
    LEARNER_PORTAL = "learner_portal"
    PRECEPTOR_CONSOLE = "preceptor_console"
    INSTITUTIONAL_ADMIN = "institutional_admin"
    MIGRATION = "migration"


class ConsentGrantEventType(str, Enum):
    GRANT = "grant"
    REVOKE = "revoke"


class ConsentScopeDescriptor(BaseModel):
    """JSON scope payload stored on each grant row."""

    model_config = ConfigDict(frozen=True)

    consent_scope: ConsentScope
    resource_id: str = "*"


class ConsentGrantRecord(BaseModel):
    """
    One append-only row in consent_grant history.

    Grant events establish scope; revoke events append a revocation that
    supersedes an earlier grant_id. Current state is projected from history.
    """

    model_config = ConfigDict(frozen=True)

    grant_id: str
    subject_id: str
    tenant_id: str
    scope: ConsentScopeDescriptor
    event_type: ConsentGrantEventType
    granted_at: datetime
    granted_by: str
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason_code: Optional[str] = None
    supersedes_grant_id: Optional[str] = None
    source: Optional[ConsentGrantSource] = None

    @property
    def effective_at(self) -> datetime:
        if self.event_type == ConsentGrantEventType.REVOKE:
            assert self.revoked_at is not None
            return self.revoked_at
        return self.granted_at
