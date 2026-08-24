"""Opaque keyset cursors for F.b learner session list pagination (I-F-7)."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from services.pratibimb.ledger_read.errors import InvalidNcvetCursorError

_NCVET_CURSOR_KEYS = frozenset({"at", "sid", "learner", "ord"})

# First page has no cursor; page_ordinal starts at 1.
NCVET_LIST_PAGE_MAX = 100


def encode_ncvet_cursor(
    *,
    at: datetime,
    session_id: str,
    learner_pseudo_id: str,
    page_ordinal: int,
) -> str:
    """Encode cursor for the *next* page request (``page_ordinal`` is server-assigned)."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    payload = {
        "at": at.isoformat(),
        "sid": session_id,
        "learner": learner_pseudo_id,
        "ord": page_ordinal,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_ncvet_cursor(
    cursor: str,
    *,
    learner_pseudo_id: str,
) -> tuple[datetime, str, int]:
    """
    Decode cursor and bind to request learner.

    Payload keys must match ``{at, sid, learner, ord}`` exactly — extra keys
    (e.g. injected ``tenant``) are rejected to prevent smuggled authorization
    fields from being ignored at decode and honored downstream.

    ``ord`` is audit-only; keyset seek uses ``(at, sid, learner)``. Forging
    ``ord`` changes audit rows, not result rows.
    """
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad)
        payload: dict[str, Any] = json.loads(raw.decode())
        if not isinstance(payload, dict) or set(payload.keys()) != _NCVET_CURSOR_KEYS:
            raise ValueError("cursor payload keys must match exactly")
        at_raw = payload["at"]
        session_id = payload["sid"]
        cursor_learner = payload["learner"]
        page_ordinal = payload["ord"]
        if not isinstance(at_raw, str) or not isinstance(session_id, str):
            raise ValueError("cursor fields must be strings")
        if not isinstance(cursor_learner, str):
            raise ValueError("cursor learner must be string")
        if cursor_learner != learner_pseudo_id:
            raise ValueError("cursor learner mismatch")
        if not isinstance(page_ordinal, int) or page_ordinal < 2:
            raise ValueError("cursor page_ordinal invalid")
        at = datetime.fromisoformat(at_raw)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at, session_id, page_ordinal
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidNcvetCursorError(cursor=cursor) from exc
