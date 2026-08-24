"""SAMVAAD runtime flags (SAMVAAD.c)."""
from __future__ import annotations

import os

# Default false — live verifier emission opt-in per deploy
SAMVAAD_VERIFIER_LIVE_EMIT: bool = os.getenv("SAMVAAD_VERIFIER_LIVE_EMIT", "false").lower() in (
    "1",
    "true",
    "yes",
)

# Default false — explicit opt-in for dry_run=false summative INSERT
SAMVAAD_LIVE_WRITE_ALLOW: bool = os.getenv("SAMVAAD_LIVE_WRITE_ALLOW", "false").lower() in (
    "1",
    "true",
    "yes",
)
