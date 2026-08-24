"""I-S-6 — fail_case_on_violation allowlist for SAMVAAD reference + corpus."""
from __future__ import annotations

from typing import Final

FAIL_CASE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "comm.handoff.sbar_complete",
        "digi.patient_id.two_source_verify",
        "digi.terminal.logout_on_shift_end",
        "team.hierarchy.safety_challenge",
    }
)
