"""FastAPI auth guard helpers."""
from __future__ import annotations

from fastapi import Request
from starlette.responses import JSONResponse, Response

from services.pratibimb.ledger_read.consent_startup import (
    find_dev_auth_headers,
    is_production_auth_mode,
)


def dev_auth_refusal_response(request: Request) -> Response | None:
    """
    In production, dev-auth headers are a refusal — not silently ignored.
    Returns a 403 response when dev headers are present; otherwise None.
    """
    if not is_production_auth_mode():
        return None
    present = find_dev_auth_headers(dict(request.headers))
    if not present:
        return None
    return JSONResponse(
        status_code=403,
        content={
            "detail": {
                "error": "dev_auth_forbidden",
                "reason": "dev auth headers are not permitted in production",
                "headers": present,
            }
        },
    )
