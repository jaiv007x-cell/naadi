"""
result_fingerprint — SHA-256 over a canonical JSON serialization of the
response body returned to the caller on a successful read.

Rules:
  - Success path only. Denial → return None; the wrapper writes NULL.
  - Canonicalization must be deterministic across Python versions and
    across equivalent Pydantic model instances vs plain dicts.
  - datetimes → ISO-8601 with tz. Sets → sorted lists. Enums → their .value.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


def _normalize(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return _normalize(obj.model_dump(mode="json"))
    if is_dataclass(obj):
        return _normalize(asdict(obj))
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        normalized = [_normalize(v) for v in obj]
        return sorted(
            normalized,
            key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    raise TypeError(
        f"result_fingerprint: no canonical form for {type(obj).__name__}"
    )


def _canonical(obj: Any) -> str:
    return json.dumps(
        _normalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_result_fingerprint(result: Any) -> str:
    """
    SHA-256 hex over canonical JSON of `result`. Caller MUST only invoke
    this on the success path; denial rows record NULL.
    """
    if result is None:
        raise ValueError(
            "compute_result_fingerprint called with None; "
            "denial path must skip fingerprinting entirely"
        )
    payload = _canonical(result).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
