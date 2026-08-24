"""Opaque keyset cursors for E3 catalog list pagination (I-E3-7)."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from services.pratibimb.ledger_read.errors import InvalidCatalogCursorError


def encode_catalog_cursor(*, at: datetime, row_id: str) -> str:
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    payload = {"at": at.isoformat(), "id": row_id}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_catalog_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad)
        payload: dict[str, Any] = json.loads(raw.decode())
        at_raw = payload["at"]
        row_id = payload["id"]
        if not isinstance(at_raw, str) or not isinstance(row_id, str):
            raise ValueError("cursor fields must be strings")
        at = datetime.fromisoformat(at_raw)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at, row_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidCatalogCursorError(cursor=cursor) from exc
