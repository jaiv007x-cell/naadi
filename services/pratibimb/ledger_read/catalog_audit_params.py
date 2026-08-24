"""Audit param bundles for catalog reads — I-E3-7 hash inputs."""
from __future__ import annotations

from typing import Any


def catalog_audit_params(**fields: Any) -> dict[str, Any]:
    """Build the dict passed to ``hash_params`` for catalog audit rows (I-E3-7).

    The opaque ``cursor`` is included deliberately: page-by-page exfiltration
    appears in ``ledger_read_audit`` as N rows with the same kind/actor/tenant
    but N distinct ``query_params_hash`` values whose cursor component advances
    monotonically. Without cursor in the hash, multi-page pulls look like one
    repeated query.
    """
    return {k: v for k, v in fields.items() if v is not None and v != ""}
