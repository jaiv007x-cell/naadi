from __future__ import annotations

"""
BEEMA ledger read contract (versioned, typed, free-text/PII stripped).

Pratibimb provides a concrete SQLAlchemy-backed implementation.
BEEMA depends only on this protocol surface.
"""

import base64
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterator, Optional, Protocol, runtime_checkable

from shared.schemas.flag_causes import FlagCause, FlagClearReason


class ConfirmationState(str, Enum):
    UNCONFIRMED = "unconfirmed"
    DISPUTED = "disputed"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class TypedEvidence:
    """One rubric hit outcome, stripped of free-text and PII."""

    hit_id: str
    axis: str  # matches rubric.Axis values
    matcher: str  # e.g. "drug_given", "sequence"
    matched: bool
    awarded: float
    weight: float  # rubric-declared weight (signed)
    at_sim_time_s: Optional[float]
    critical: bool = False
    negative: bool = False


@dataclass(frozen=True)
class FlagEpisode:
    flag: str
    cause: FlagCause | str  # allow legacy string causes
    set_at_s: float
    cleared_at_s: Optional[float]
    clear_reason: Optional[FlagClearReason]
    caused_by_action_id: Optional[str] = None


@dataclass(frozen=True)
class ActionSummary:
    """Typed action projection — no free-text payloads."""

    action_id: str
    kind: str  # "drug" | "order" | "diagnosis" | "handoff" | "escalation"
    at_sim_time_s: float
    canonical_key: str  # stable, machine-readable action key
    within_expected_window: Optional[bool] = None


@dataclass(frozen=True)
class SessionLedgerRecord:
    """
    Atomic BEEMA read unit. One completed session = one record.
    Immutable, hash-addressable, physio-versioned.
    """

    # Identity (pseudonymous)
    session_id: str
    learner_pseudo_id: str
    cohort_id: str
    case_id: str
    case_version: str

    # Provenance
    physio_engine_version: str
    rubric_version: str
    replay_hash: str  # sha256 of trace
    finalized_at_utc: str  # ISO8601

    # Confirmation state
    confirmation: ConfirmationState
    preceptor_pseudo_id: Optional[str]

    # Grade snapshot
    grade_total_normalized: float
    grade_passed: bool
    axis_normalized: dict[str, float]

    # Typed projections
    evidence: tuple[TypedEvidence, ...]
    flags: tuple[FlagEpisode, ...]
    actions: tuple[ActionSummary, ...]

    # Optional linkage tokens (opaque; BEEMA never resolves them itself)
    outcome_link_token: Optional[str] = None

    # Phase E1: published-case blueprint bytes at session create.
    # Distinct from replay_hash (trace). NULL for seed/legacy sessions.
    # Snapshotted at create; finalize verifies against current published row
    # and refuses on drift (BLUEPRINT_HASH_DRIFT). Never rewritten after append.
    blueprint_content_hash: Optional[str] = None
    # Phase E2.2.c: provenance of blueprint_content_hash.
    # NULL = pre-E2.2.c legacy (unknown). Write-once at append; never rewritten.
    # Legal: (NULL,NULL), (NULL,seed), (hash,published), (hash,NULL E1 transitional).
    # Illegal: (hash,seed), (NULL,published).
    blueprint_source: Optional[str] = None


@dataclass(frozen=True)
class LedgerQuery:
    """Filter spec for read APIs. AND-composed; all fields optional."""

    cohort_id: Optional[str] = None
    cohort_id_in: Optional[tuple[str, ...]] = None
    case_id: Optional[str] = None
    learner_pseudo_id: Optional[str] = None
    physio_engine_version_in: Optional[tuple[str, ...]] = None
    rubric_version_in: Optional[tuple[str, ...]] = None
    confirmation_in: Optional[tuple[ConfirmationState, ...]] = None
    finalized_after_utc: Optional[str] = None
    finalized_before_utc: Optional[str] = None
    limit: int = 1000
    cursor: Optional[str] = None


@dataclass(frozen=True)
class LedgerPage:
    records: tuple[SessionLedgerRecord, ...]
    next_cursor: Optional[str]


@runtime_checkable
class LedgerReader(Protocol):
    """BEEMA depends on this Protocol, not a concrete class."""

    def get(self, session_id: str) -> Optional[SessionLedgerRecord]: ...

    def query(self, q: LedgerQuery) -> LedgerPage: ...

    def iter_query(self, q: LedgerQuery) -> Iterator[SessionLedgerRecord]: ...

    def trusted_physio_versions(self) -> frozenset[str]: ...

