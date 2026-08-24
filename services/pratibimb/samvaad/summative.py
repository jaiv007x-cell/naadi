"""SAMVAAD.b — summative evidence capture (I-S-10/13/14/15).

SAMVAAD-specific projector — does not open a parallel audit table.
Writes validated payloads only; patient_reported fail-closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from services.pratibimb.samvaad.evidence_class import (
    assert_summative_evidence_class,
)
from services.pratibimb.samvaad.summative_contract import (
    REQUIRED_SUMMATIVE_FIELDS,
    SummativeEvidenceError,
)


@dataclass(frozen=True)
class SummativeEvidenceRecord:
    transcript_digest: str
    grader_version: str
    rubric_schema_version: str
    artifact_schema_version: str
    evidence_class: str
    competency_hits: tuple[str, ...] = ()
    framework_citation_anchor: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# In-memory store for SAMVAAD.b — ledger projector wiring is SAMVAAD.c+
_STORE: list[SummativeEvidenceRecord] = []


def clear_summative_store() -> None:
    _STORE.clear()


def summative_store() -> list[SummativeEvidenceRecord]:
    return list(_STORE)


def _require_fields(payload: dict[str, Any]) -> None:
    missing = REQUIRED_SUMMATIVE_FIELDS - set(payload)
    if missing:
        raise SummativeEvidenceError(
            f"I-S-14 missing summative fields: {sorted(missing)}"
        )


def capture_summative(payload: dict[str, Any]) -> SummativeEvidenceRecord:
    """
    Fail-closed summative write.

    Requires H-shaped digest fields + evidence_class.
    Rejects patient_reported (I-S-13).
    """
    _require_fields(payload)
    evidence_class = payload["evidence_class"]
    assert_summative_evidence_class(evidence_class)

    # Forbid biometric / patient-feedback keys in extras
    from services.pratibimb.samvaad.schema import validate_summative_payload

    validate_summative_payload(payload)

    record = SummativeEvidenceRecord(
        transcript_digest=str(payload["transcript_digest"]),
        grader_version=str(payload["grader_version"]),
        rubric_schema_version=str(payload["rubric_schema_version"]),
        artifact_schema_version=str(payload["artifact_schema_version"]),
        evidence_class=str(evidence_class),
        competency_hits=tuple(payload.get("competency_hits") or ()),
        framework_citation_anchor=str(payload.get("framework_citation_anchor") or ""),
        extras={
            k: v
            for k, v in payload.items()
            if k not in REQUIRED_SUMMATIVE_FIELDS
            and k not in {"competency_hits", "framework_citation_anchor"}
        },
    )
    _STORE.append(record)
    return record


def rcp_shaped_fields(record: SummativeEvidenceRecord) -> frozenset[str]:
    """I-S-15 — fields required for G/H portable-contract compatibility."""
    return frozenset(
        {
            "transcript_digest",
            "grader_version",
            "rubric_schema_version",
            "artifact_schema_version",
            "evidence_class",
        }
    ) & frozenset(record.to_dict())
