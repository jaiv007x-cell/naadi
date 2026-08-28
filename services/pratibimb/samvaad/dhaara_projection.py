"""Deterministic SAMVAAD evidence projection into Dhaara freshness events."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.beema.analytics.freshness import competency_state_from_snapshot
from services.pratibimb.ledger.models import (
    SamvaadFormativeEvidenceRow,
    SamvaadSummativeEvidenceRow,
)
from services.pratibimb.samvaad.evidence_class import CLASS_CONFIDENCE_WEIGHT
from services.pratibimb.samvaad.metrics import (
    record_dhaara_publish,
    record_dhaara_reconcile,
    set_dhaara_reconcile_success,
    set_dhaara_stale_seconds,
)
from shared.schemas.freshness import CompetencyState, SkillFreshnessSnapshot


class FreshnessInputError(ValueError):
    """Caller supplied incomplete or invalid persisted freshness scalars."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def normalize_freshness_inputs(
    competency_hits: Iterable[str],
    value: Any,
) -> dict[str, dict[str, Any]]:
    hits = tuple(competency_hits)
    if not hits and value is None:
        return {}
    if not isinstance(value, dict) or set(value) != set(hits):
        raise FreshnessInputError(
            "freshness_inputs must contain exactly one object per competency hit"
        )
    required = {
        "competency_after",
        "ability",
        "confidence",
        "freshness",
        "evidence_age_days",
        "reassessment_due",
    }
    normalized: dict[str, dict[str, Any]] = {}
    for competency_id in hits:
        item = value[competency_id]
        if not isinstance(item, dict) or not required <= set(item):
            raise FreshnessInputError(
                f"freshness inputs for {competency_id!r} require {sorted(required)}"
            )
        result = dict(item)
        for name in ("competency_after", "ability", "confidence", "freshness"):
            result[name] = float(result[name])
            if not 0.0 <= result[name] <= 1.0:
                raise FreshnessInputError(
                    f"{name} for {competency_id!r} must be in [0, 1]"
                )
        before = result.get("competency_before")
        if before is not None:
            result["competency_before"] = float(before)
            if not 0.0 <= result["competency_before"] <= 1.0:
                raise FreshnessInputError(
                    f"competency_before for {competency_id!r} must be in [0, 1]"
                )
        result["evidence_age_days"] = float(result["evidence_age_days"])
        if result["evidence_age_days"] < 0.0:
            raise FreshnessInputError(
                f"evidence_age_days for {competency_id!r} must be non-negative"
            )
        if not isinstance(result["reassessment_due"], bool):
            raise FreshnessInputError(
                f"reassessment_due for {competency_id!r} must be boolean"
            )
        normalized[competency_id] = result
    return normalized


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return stamp.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


@dataclass(frozen=True)
class EvidenceProjectionSource:
    evidence_id: str
    assessment_kind: str
    evidence_class: str
    learner_pseudo_id: str
    session_anchor: str
    replay_hash: str
    competency_hits: tuple[str, ...]
    freshness_inputs: dict[str, dict[str, Any]]
    captured_at_utc: datetime


@dataclass(frozen=True)
class FreshnessProjection:
    """Envelope fields plus the shared additive freshness snapshot."""

    evidence_id: str
    assessment_kind: str
    snapshot: SkillFreshnessSnapshot

    @property
    def key(self) -> tuple[str, str, str]:
        return (
            self.assessment_kind,
            self.evidence_id,
            self.snapshot.competency_id,
        )

    def event_dict(self) -> dict[str, Any]:
        return {
            "type": "freshness.snapshot",
            "assessment_kind": self.assessment_kind,
            "source_evidence_id": self.evidence_id,
            **self.snapshot.model_dump(mode="json"),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.event_dict()).encode("utf-8")


class ProjectionSink(Protocol):
    def get(self, key: tuple[str, str, str]) -> FreshnessProjection | None: ...

    def publish(self, projection: FreshnessProjection) -> None: ...

    def keys(self) -> set[tuple[str, str, str]]: ...


@dataclass
class InMemoryDhaaraProjection:
    """Deterministic test/local sink with exact-convergence inspection."""

    projections: dict[tuple[str, str, str], FreshnessProjection] = field(
        default_factory=dict
    )

    def get(self, key: tuple[str, str, str]) -> FreshnessProjection | None:
        return self.projections.get(key)

    def publish(self, projection: FreshnessProjection) -> None:
        self.projections[projection.key] = projection

    def keys(self) -> set[tuple[str, str, str]]:
        return set(self.projections)

    def ordered(self) -> tuple[FreshnessProjection, ...]:
        return tuple(
            sorted(
                self.projections.values(),
                key=lambda item: (
                    item.snapshot.computed_at,
                    item.evidence_id,
                    item.snapshot.competency_id,
                ),
            )
        )


@dataclass
class DhaaraHttpProjectionSink:
    """Fail-soft HTTP publisher; only acknowledged events enter the local mirror."""

    base_url: str = field(
        default_factory=lambda: os.getenv("DHAARA_URL", "http://localhost:8200")
    )
    timeout_seconds: float = 1.0
    projections: dict[tuple[str, str, str], FreshnessProjection] = field(
        default_factory=dict
    )

    def get(self, key: tuple[str, str, str]) -> FreshnessProjection | None:
        return self.projections.get(key)

    def publish(self, projection: FreshnessProjection) -> None:
        import httpx

        response = httpx.post(
            f"{self.base_url.rstrip('/')}/v1/freshness/ingest",
            json=projection.snapshot.model_dump(mode="json"),
            headers={
                "Idempotency-Key": "|".join(projection.key),
                "X-Samvaad-Assessment-Kind": projection.assessment_kind,
                "X-Samvaad-Evidence-Id": projection.evidence_id,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        self.projections[projection.key] = projection

    def keys(self) -> set[tuple[str, str, str]]:
        return set(self.projections)


_PRODUCTION_SINK: DhaaraHttpProjectionSink | None = None


def get_production_projection_sink() -> DhaaraHttpProjectionSink:
    global _PRODUCTION_SINK
    if _PRODUCTION_SINK is None:
        _PRODUCTION_SINK = DhaaraHttpProjectionSink()
    return _PRODUCTION_SINK


def build_freshness_projections(
    source: EvidenceProjectionSource,
) -> tuple[FreshnessProjection, ...]:
    weight = CLASS_CONFIDENCE_WEIGHT[source.evidence_class]
    computed_at = source.captured_at_utc
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)
    projections = []
    for competency_id in sorted(source.competency_hits):
        inputs = source.freshness_inputs.get(competency_id)
        if inputs is None:
            raise ValueError(
                f"missing persisted freshness inputs for competency {competency_id!r}"
            )
        projections.append(
            FreshnessProjection(
                evidence_id=source.evidence_id,
                assessment_kind=source.assessment_kind,
                snapshot=SkillFreshnessSnapshot(
                    learner_pseudo_id=source.learner_pseudo_id,
                    competency_id=competency_id,
                    competency_before=inputs.get("competency_before"),
                    competency_after=inputs["competency_after"],
                    ability=inputs["ability"],
                    confidence=inputs["confidence"],
                    freshness=inputs["freshness"],
                    evidence_age_days=inputs["evidence_age_days"],
                    reassessment_due=inputs["reassessment_due"],
                    source_session_ids=(source.session_anchor,),
                    source_replay_hash=source.replay_hash,
                    computed_at=computed_at,
                    evidence_class=source.evidence_class,
                    evidence_weight=weight,
                ),
            )
        )
    return tuple(projections)


def publish_source(
    source: EvidenceProjectionSource,
    sink: ProjectionSink,
) -> dict[str, int]:
    outcomes = {"success": 0, "skipped": 0, "error": 0}
    for projection in build_freshness_projections(source):
        current = sink.get(projection.key)
        if current is not None:
            outcome = (
                "skipped"
                if current.canonical_bytes() == projection.canonical_bytes()
                else "error"
            )
        else:
            try:
                sink.publish(projection)
                outcome = "success"
            except Exception:
                outcome = "error"
        outcomes[outcome] += 1
        record_dhaara_publish(
            assessment_kind=source.assessment_kind,
            evidence_class=source.evidence_class,
            outcome=outcome,
        )
    return outcomes


def _loads_hits(raw: str) -> tuple[str, ...]:
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("competency_hits_json must be a JSON string list")
    return tuple(value)


def _loads_freshness_inputs(raw: str) -> dict[str, dict[str, Any]]:
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, dict)
        for key, item in value.items()
    ):
        raise ValueError("freshness_inputs_json must be a competency-keyed JSON object")
    return value


def sources_from_session(session: Session) -> tuple[EvidenceProjectionSource, ...]:
    sources: list[EvidenceProjectionSource] = []
    summative = session.scalars(select(SamvaadSummativeEvidenceRow)).all()
    formative = session.scalars(select(SamvaadFormativeEvidenceRow)).all()
    for row in summative:
        sources.append(
            EvidenceProjectionSource(
                evidence_id=row.evidence_id,
                assessment_kind=row.assessment_kind,
                evidence_class=row.evidence_class,
                learner_pseudo_id=row.learner_pseudo_id,
                session_anchor=row.session_anchor,
                replay_hash=row.transcript_digest,
                competency_hits=_loads_hits(row.competency_hits_json),
                freshness_inputs=_loads_freshness_inputs(row.freshness_inputs_json),
                captured_at_utc=row.captured_at_utc,
            )
        )
    for row in formative:
        sources.append(
            EvidenceProjectionSource(
                evidence_id=row.evidence_id,
                assessment_kind=row.assessment_kind,
                evidence_class=row.evidence_class,
                learner_pseudo_id=row.learner_pseudo_id,
                session_anchor=row.session_anchor,
                replay_hash=row.evidence_digest,
                competency_hits=_loads_hits(row.competency_hits_json),
                freshness_inputs=_loads_freshness_inputs(row.freshness_inputs_json),
                captured_at_utc=row.captured_at_utc,
            )
        )
    return tuple(
        sorted(
            sources,
            key=lambda source: (source.captured_at_utc, source.evidence_id),
        )
    )


def rebuild_projection(
    session: Session,
) -> tuple[tuple[FreshnessProjection, ...], tuple[CompetencyState, ...]]:
    projections = tuple(
        projection
        for source in sources_from_session(session)
        for projection in build_freshness_projections(source)
    )
    states = tuple(competency_state_from_snapshot(item.snapshot) for item in projections)
    return projections, states


def reconcile_projection(
    session: Session,
    sink: ProjectionSink,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    clock = now or datetime.now(timezone.utc)
    expected, _ = rebuild_projection(session)
    expected_by_key = {item.key: item for item in expected}
    outcomes = {"success": 0, "skipped": 0, "error": 0}
    kinds = {"summative", "formative"}
    latest_by_kind: dict[str, datetime] = {}

    for projection in expected:
        kind = projection.assessment_kind
        latest_by_kind[kind] = max(
            latest_by_kind.get(kind, projection.snapshot.computed_at),
            projection.snapshot.computed_at,
        )
        current = sink.get(projection.key)
        if current is None:
            try:
                sink.publish(projection)
                outcome = "success"
            except Exception:
                outcome = "error"
        elif current.canonical_bytes() == projection.canonical_bytes():
            outcome = "skipped"
        else:
            outcome = "error"
        outcomes[outcome] += 1
        record_dhaara_reconcile(assessment_kind=kind, outcome=outcome)

    extra_keys = sink.keys() - set(expected_by_key)
    for assessment_kind, _, _ in extra_keys:
        kinds.add(assessment_kind)
        outcomes["error"] += 1
        record_dhaara_reconcile(assessment_kind=assessment_kind, outcome="error")

    if outcomes["error"] == 0:
        for kind in kinds:
            set_dhaara_reconcile_success(
                assessment_kind=kind,
                timestamp_seconds=clock.timestamp(),
            )
            latest = latest_by_kind.get(kind)
            stale = 0.0 if latest is None else max(0.0, (clock - latest).total_seconds())
            set_dhaara_stale_seconds(assessment_kind=kind, seconds=stale)
    return outcomes


def projection_bytes(items: Iterable[FreshnessProjection]) -> bytes:
    return b"\n".join(item.canonical_bytes() for item in items)
