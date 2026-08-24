"""
Ledger startup guards.

Prevents silent evidence loss when LEDGER_DISABLED is set alongside summative
cases, and emits a loud warning in non-dev environments.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from services.pratibimb.app.case_gen.sampler import CORPUS_PATH
from services.pratibimb.ledger.db import is_dev_environment, ledger_enabled

log = logging.getLogger(__name__)


class LedgerStartupError(RuntimeError):
    """Refusing to boot with ledger disabled while summative cases are loaded."""


def _case_assessment_mode(raw: dict) -> str | None:
    if raw.get("assessment_mode"):
        return str(raw["assessment_mode"]).lower()
    identity = raw.get("identity")
    if isinstance(identity, dict) and identity.get("assessment_mode"):
        return str(identity["assessment_mode"]).lower()
    return None


def summative_case_ids(corpus_path: Path | None = None) -> list[str]:
    """Return case_ids in the loaded corpus declared as summative."""
    path = corpus_path or CORPUS_PATH
    with path.open(encoding="utf-8") as f:
        raw: list[dict] = json.load(f)
    out: list[str] = []
    for case in raw:
        if case.get("probe_only"):
            continue
        if _case_assessment_mode(case) == "summative":
            out.append(str(case.get("case_id", "?")))
    return out


def validate_ledger_startup(*, corpus_path: Path | None = None) -> None:
    """
    Boot-time ledger policy.

    - LEDGER_DISABLED + summative cases in corpus → refuse to start (fail-closed).
    - LEDGER_DISABLED in non-dev → loud warning that no evidence will persist.
    """
    if ledger_enabled():
        return

    summative = summative_case_ids(corpus_path)
    if summative:
        raise LedgerStartupError(
            "LEDGER_DISABLED is set but the loaded corpus contains summative cases "
            f"({', '.join(summative)}). Refusing to start — summative sessions would "
            "not be recorded to the evidence ledger."
        )

    if not is_dev_environment():
        log.warning(
            "LEDGER_DISABLED is set in a non-dev environment (ENV=%s) — "
            "NO EVIDENCE WILL BE PERSISTED. This is safe for local dev only. "
            "If you are seeing this in staging or prod, unset the variable now.",
            os.getenv("PRATIBIMB_ENV", os.getenv("ENV", "unknown")),
        )
