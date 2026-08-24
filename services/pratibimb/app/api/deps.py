"""FastAPI dependency wiring for ledger_read routes."""
from __future__ import annotations

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.deps import (
    get_auth_context,
    get_jwt_gateway,
    ledger_reader,
)
from services.pratibimb.ledger_read.jwt_gateway import JwtLedgerGateway

__all__ = [
    "AuthContext",
    "get_auth_context",
    "get_jwt_gateway",
    "ledger_reader",
    "JwtLedgerGateway",
]
