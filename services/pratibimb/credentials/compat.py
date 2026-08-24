"""Credentials-side status-list v1→v2 shim (I.c.2.1 P2).

Wholesale-deletable at day-30. ARP must not import this module.
"""
from __future__ import annotations

from typing import Any

from services.pratibimb.credentials.status_list import (
    STATUS_LIST_SCHEMA_V1,
    STATUS_LIST_SCHEMA_V2,
    normalize_status_list_entries,
)

_V1_SCHEMAS = frozenset({STATUS_LIST_SCHEMA_V1, None, ""})


def coerce_status_list_entries_for_verify(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Return v2-shaped entries. Increments compat counter only on v1/missing-kind coercion."""
    schema = envelope.get("status_list_schema_version")
    if schema == STATUS_LIST_SCHEMA_V2:
        entries = list(envelope.get("entries") or [])
        if all(e.get("identifier_kind") and e.get("identifier_id") for e in entries):
            return entries
    if schema in _V1_SCHEMAS or schema == STATUS_LIST_SCHEMA_V2:
        return normalize_status_list_entries(envelope)
    return list(envelope.get("entries") or [])
