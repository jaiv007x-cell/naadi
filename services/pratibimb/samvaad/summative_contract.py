"""I-S-14 / I-S-7(B) — summative contract + Dhaara freshness pins."""
from __future__ import annotations

from typing import Final

REQUIRED_SUMMATIVE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "transcript_digest",
        "grader_version",
        "rubric_schema_version",
        "artifact_schema_version",
        "evidence_class",
    }
)

# I-S-7(B): event-primary update + daily decay sweep (UTC-anchored — see ops runbook)
DHAARA_FRESHNESS_TRIGGER: Final[str] = "event_on_evidence + daily_decay_cron"
DHAARA_DECAY_CRON_TIMEZONE: Final[str] = "UTC"
DHAARA_DECAY_CRON_SCHEDULE: Final[str] = "0 2 * * *"  # 02:00 UTC daily


class SummativeEvidenceError(ValueError):
    """Summative evidence violates SAMVAAD Framework v1 contract."""


ERROR_KIND_SUMMATIVE_EVIDENCE_CLASS_FORBIDDEN: Final[str] = "summative_evidence_class_forbidden"


class SummativeEvidenceRejectedError(SummativeEvidenceError):
    """HTTP 422 summative rejection with SOC-filterable ``error_kind``."""

    def __init__(self, message: str, *, error_kind: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind
