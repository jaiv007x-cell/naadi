"""
LedgerWriter — append-only sink for graded sessions.

Called after Nirikshak produces a CaseGrade. Writes one immutable
SessionLedgerRow per completed session. BEEMA's SqlLedgerReader tails this
table via monotonic session_id ordering.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from services.beema.ledger.interface import (
    ActionSummary,
    ConfirmationState,
    FlagEpisode,
    SessionLedgerRecord,
    TypedEvidence,
)
from services.pratibimb.app.eval.nirikshak import CaseGrade, _extract_time
from services.pratibimb.app.eval.rubric import GradingBlueprint
from services.pratibimb.app.eval.scorer import Turn, project_turns
from services.pratibimb.app.physio.state import PhysioTrace as LegacyPhysioTrace
from services.pratibimb.ledger.models import SessionLedgerRow, TrustedPhysioVersion
from shared.schemas.flag_causes import FlagClearReason
from shared.schemas.trace import PhysioTrace as TypedPhysioTrace

log = logging.getLogger(__name__)

BLUEPRINT_SOURCE_PUBLISHED = "published"
BLUEPRINT_SOURCE_SEED = "seed"
_ALLOWED_BLUEPRINT_SOURCES = frozenset(
    {BLUEPRINT_SOURCE_PUBLISHED, BLUEPRINT_SOURCE_SEED}
)


def validate_blueprint_provenance(
    blueprint_content_hash: str | None,
    blueprint_source: str | None,
) -> None:
    """
    I-E22-4: published ⇒ hash present; seed ⇒ hash null.
    Null source + null hash = pre-E1 legacy. Null source + hash = E1 transitional.
    Illegal: seed+hash, published+null hash, unknown source strings.
    """
    if blueprint_source is not None and blueprint_source not in _ALLOWED_BLUEPRINT_SOURCES:
        raise ValueError(
            f"blueprint_source must be 'published', 'seed', or None; "
            f"got {blueprint_source!r}"
        )
    if blueprint_source == BLUEPRINT_SOURCE_SEED and blueprint_content_hash is not None:
        raise ValueError(
            "BLUEPRINT_SOURCE_MISMATCH: blueprint_source='seed' requires "
            "blueprint_content_hash IS NULL"
        )
    if blueprint_source == BLUEPRINT_SOURCE_PUBLISHED:
        if blueprint_content_hash is None or len(blueprint_content_hash) != 64:
            raise ValueError(
                "BLUEPRINT_SOURCE_MISMATCH: blueprint_source='published' requires "
                "a 64-char blueprint_content_hash"
            )


class LedgerWriter(Protocol):
    def append(self, record: SessionLedgerRecord) -> str: ...


class LedgerConflictError(RuntimeError):
    """Same session_id already persisted with different content."""


class UntrustedPhysioError(RuntimeError):
    """Physio engine version is not in the trusted registry."""


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_json_default)


def replay_hash(legacy_trace: LegacyPhysioTrace) -> str:
    """SHA-256 over the canonical legacy trace serialization."""
    payload = legacy_trace.to_canonical()
    digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    return f"sha256:{digest}"


def _parse_clear_reason(raw: str | None) -> FlagClearReason | None:
    if raw is None:
        return None
    aliases = {
        "flag_expired_or_resolved": FlagClearReason.RESOLVED,
        "clinician_action": FlagClearReason.CORRECTIVE_ACTION,
        "ttl_expired": FlagClearReason.TTL_EXPIRED,
    }
    if raw in aliases:
        return aliases[raw]
    try:
        return FlagClearReason(raw)
    except ValueError:
        return None


def _find_causing_action(legacy: LegacyPhysioTrace, t_set: float) -> str | None:
    """Best-effort link from a raised flag to the nearest preceding drug event."""
    best: tuple[float, str] | None = None
    for ev in legacy.events:
        if ev.get("kind") not in ("medication", "dose"):
            continue
        t = float(ev.get("t", ev.get("t_min", 0) * 60))
        if t <= t_set and (best is None or t > best[0]):
            drug = ev.get("drug", "unknown")
            best = (t, f"give_{drug}")
    return best[1] if best else None


def _flag_episodes_from_legacy(legacy: LegacyPhysioTrace) -> tuple[FlagEpisode, ...]:
    open_sets: dict[str, dict] = {}
    episodes: list[FlagEpisode] = []

    for fh in legacy.flag_history:
        flag = fh.get("flag", "")
        if fh.get("set"):
            open_sets[flag] = fh
            continue

        set_entry = open_sets.pop(flag, None)
        if set_entry is None:
            continue

        t_set = float(set_entry.get("t", 0.0))
        t_clear = float(fh.get("t", t_set))
        cause = set_entry.get("cause") or "unknown"
        episodes.append(
            FlagEpisode(
                flag=flag,
                cause=cause,
                set_at_s=t_set,
                cleared_at_s=t_clear,
                clear_reason=_parse_clear_reason(fh.get("reason")),
                caused_by_action_id=_find_causing_action(legacy, t_set),
            )
        )

    for flag, set_entry in open_sets.items():
        t_set = float(set_entry.get("t", 0.0))
        cause = set_entry.get("cause") or "unknown"
        episodes.append(
            FlagEpisode(
                flag=flag,
                cause=cause,
                set_at_s=t_set,
                cleared_at_s=None,
                clear_reason=None,
                caused_by_action_id=_find_causing_action(legacy, t_set),
            )
        )

    return tuple(sorted(episodes, key=lambda e: (e.set_at_s, e.flag)))


def _typed_evidence(
    grade: CaseGrade, blueprint: GradingBlueprint
) -> tuple[TypedEvidence, ...]:
    hit_by_id = {h.id: h for h in blueprint.hits}
    out: list[TypedEvidence] = []
    for outcome in grade.outcomes:
        hit = hit_by_id.get(outcome.hit_id)
        matcher = hit.matcher if hit else "unknown"
        weight = hit.points if hit else 0.0
        critical = bool(hit and hit.required and hit.fail_case_on_violation)
        out.append(
            TypedEvidence(
                hit_id=outcome.hit_id,
                axis=outcome.axis.value,
                matcher=matcher,
                matched=outcome.matched,
                awarded=outcome.points_awarded,
                weight=weight,
                at_sim_time_s=_extract_time(outcome.evidence),
                critical=critical,
                negative=False,
            )
        )
    return tuple(out)


def _actions_from_trace(trace: TypedPhysioTrace) -> tuple[ActionSummary, ...]:
    actions: list[ActionSummary] = []

    for i, drug in enumerate(trace.drug_admins()):
        actions.append(
            ActionSummary(
                action_id=f"drug_{i}_{drug.drug_id}",
                kind="drug",
                at_sim_time_s=drug.t_s,
                canonical_key=f"{drug.drug_id}:{drug.route}:{drug.dose}{drug.dose_unit}",
            )
        )

    for i, order in enumerate(trace.orders()):
        actions.append(
            ActionSummary(
                action_id=f"order_{i}_{order.order_id}",
                kind="order",
                at_sim_time_s=order.t_s,
                canonical_key=order.order_id,
            )
        )

    for i, dx in enumerate(trace.diagnoses()):
        actions.append(
            ActionSummary(
                action_id=f"dx_{i}_{dx.dx}",
                kind="diagnosis",
                at_sim_time_s=dx.t_s,
                canonical_key=dx.dx,
            )
        )

    for i, handoff in enumerate(trace.handoffs()):
        actions.append(
            ActionSummary(
                action_id=f"handoff_{i}_{handoff.target}",
                kind="handoff",
                at_sim_time_s=handoff.t_s,
                canonical_key=f"{handoff.target}:{'+'.join(sorted(handoff.fields))}",
            )
        )

    for i, esc in enumerate(trace.escalations()):
        actions.append(
            ActionSummary(
                action_id=f"escalation_{i}_{esc.target}",
                kind="escalation",
                at_sim_time_s=esc.t_s,
                canonical_key=esc.target,
            )
        )

    return tuple(sorted(actions, key=lambda a: (a.at_sim_time_s, a.action_id)))


def build_record(
    *,
    session_id: str,
    learner_pseudo_id: str,
    cohort_id: str,
    case_id: str,
    case_version: str,
    grade: CaseGrade,
    blueprint: GradingBlueprint,
    legacy_trace: LegacyPhysioTrace,
    turns: list[Turn],
    finalized_at: datetime | None = None,
    confirmation: ConfirmationState = ConfirmationState.UNCONFIRMED,
    preceptor_pseudo_id: str | None = None,
    blueprint_content_hash: str | None = None,
    blueprint_source: str | None = None,
) -> SessionLedgerRecord:
    """Assemble a SessionLedgerRecord from finalized session artifacts."""
    validate_blueprint_provenance(blueprint_content_hash, blueprint_source)
    typed_trace, _report = legacy_trace.to_typed_trace(
        case_id=case_id,
        case_version=case_version,
    )
    project_turns(typed_trace, turns)

    finalized = finalized_at or datetime.now(timezone.utc)
    axis_normalized = {
        a.axis.value.upper(): a.normalized for a in grade.axis_scores
    }

    return SessionLedgerRecord(
        session_id=session_id,
        learner_pseudo_id=learner_pseudo_id,
        cohort_id=cohort_id,
        case_id=case_id,
        case_version=case_version,
        physio_engine_version=grade.physio_version,
        rubric_version=grade.rubric_version,
        replay_hash=replay_hash(legacy_trace),
        finalized_at_utc=finalized.isoformat(),
        confirmation=confirmation,
        preceptor_pseudo_id=preceptor_pseudo_id,
        grade_total_normalized=grade.overall,
        grade_passed=grade.passed,
        axis_normalized=axis_normalized,
        evidence=_typed_evidence(grade, blueprint),
        flags=_flag_episodes_from_legacy(legacy_trace),
        actions=_actions_from_trace(typed_trace),
        blueprint_content_hash=blueprint_content_hash,
        blueprint_source=blueprint_source,
    )


def _record_fingerprint(record: SessionLedgerRecord) -> str:
    payload = asdict(record)
    payload.pop("finalized_at_utc", None)
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


class SqlLedgerWriter:
    """
    Postgres/SQLite-backed append-only writer. Idempotent on session_id when
    the persisted fingerprint matches; a retry with the same session_id but
    different content raises LedgerConflictError.
    """

    def __init__(self, db: DbSession) -> None:
        self.db = db

    def append(self, record: SessionLedgerRecord) -> str:
        validate_blueprint_provenance(
            record.blueprint_content_hash, record.blueprint_source
        )
        self._assert_trusted_physio(record.physio_engine_version)

        existing = self.db.execute(
            select(SessionLedgerRow).where(
                SessionLedgerRow.session_id == record.session_id
            )
        ).scalar_one_or_none()

        fingerprint = _record_fingerprint(record)
        if existing is not None:
            existing_fp = self._row_fingerprint(existing)
            if existing_fp == fingerprint:
                log.info("ledger.append.idempotent session=%s", record.session_id)
                return record.replay_hash
            raise LedgerConflictError(
                f"session {record.session_id!r} already exists with different content"
            )

        row = SessionLedgerRow(
            session_id=record.session_id,
            learner_pseudo_id=record.learner_pseudo_id,
            cohort_id=record.cohort_id,
            case_id=record.case_id,
            case_version=record.case_version,
            physio_engine_version=record.physio_engine_version,
            rubric_version=record.rubric_version,
            replay_hash=record.replay_hash,
            blueprint_content_hash=record.blueprint_content_hash,
            blueprint_source=record.blueprint_source,
            finalized_at_utc=datetime.fromisoformat(record.finalized_at_utc),
            confirmation=record.confirmation.value,
            preceptor_pseudo_id=record.preceptor_pseudo_id,
            grade_total=record.grade_total_normalized,
            grade_passed=record.grade_passed,
            axis_normalized=dict(record.axis_normalized),
            evidence=[asdict(e) for e in record.evidence],
            flags=[
                {
                    "flag": f.flag,
                    "cause": f.cause.value if hasattr(f.cause, "value") else str(f.cause),
                    "set_at_s": f.set_at_s,
                    "cleared_at_s": f.cleared_at_s,
                    "clear_reason": (
                        f.clear_reason.value if f.clear_reason is not None else None
                    ),
                    "caused_by_action_id": f.caused_by_action_id,
                }
                for f in record.flags
            ],
            actions=[asdict(a) for a in record.actions],
            outcome_link_token=record.outcome_link_token,
        )
        self.db.add(row)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise LedgerConflictError(str(exc)) from exc

        log.info(
            "ledger.append.ok session=%s replay_hash=%s",
            record.session_id,
            record.replay_hash,
        )
        return record.replay_hash

    def _assert_trusted_physio(self, version: str) -> None:
        row = self.db.execute(
            select(TrustedPhysioVersion).where(
                TrustedPhysioVersion.version == version
            )
        ).scalar_one_or_none()
        if row is None:
            raise UntrustedPhysioError(
                f"physio backend version {version!r} is not in trusted registry"
            )

    @staticmethod
    def _row_fingerprint(row: SessionLedgerRow) -> str:
        record = SessionLedgerRecord(
            session_id=row.session_id,
            learner_pseudo_id=row.learner_pseudo_id,
            cohort_id=row.cohort_id,
            case_id=row.case_id,
            case_version=row.case_version,
            physio_engine_version=row.physio_engine_version,
            rubric_version=row.rubric_version,
            replay_hash=row.replay_hash,
            finalized_at_utc=row.finalized_at_utc.isoformat(),
            confirmation=ConfirmationState(row.confirmation),
            preceptor_pseudo_id=row.preceptor_pseudo_id,
            grade_total_normalized=float(row.grade_total),
            grade_passed=bool(row.grade_passed),
            axis_normalized=dict(row.axis_normalized or {}),
            evidence=tuple(
                TypedEvidence(
                    hit_id=e["hit_id"],
                    axis=e["axis"],
                    matcher=e["matcher"],
                    matched=bool(e["matched"]),
                    awarded=float(e["awarded"]),
                    weight=float(e["weight"]),
                    at_sim_time_s=e.get("at_sim_time_s"),
                    critical=bool(e.get("critical", False)),
                    negative=bool(e.get("negative", False)),
                )
                for e in (row.evidence or [])
            ),
            flags=tuple(
                FlagEpisode(
                    flag=f["flag"],
                    cause=f["cause"],
                    set_at_s=float(f["set_at_s"]),
                    cleared_at_s=f.get("cleared_at_s"),
                    clear_reason=(
                        FlagClearReason(f["clear_reason"])
                        if f.get("clear_reason") is not None
                        else None
                    ),
                    caused_by_action_id=f.get("caused_by_action_id"),
                )
                for f in (row.flags or [])
            ),
            actions=tuple(
                ActionSummary(
                    action_id=a["action_id"],
                    kind=a["kind"],
                    at_sim_time_s=float(a["at_sim_time_s"]),
                    canonical_key=a["canonical_key"],
                    within_expected_window=a.get("within_expected_window"),
                )
                for a in (row.actions or [])
            ),
            outcome_link_token=row.outcome_link_token,
            blueprint_content_hash=row.blueprint_content_hash,
            blueprint_source=row.blueprint_source,
        )
        return _record_fingerprint(record)


def append_graded_session(
    db: DbSession,
    *,
    session_id: str,
    learner_pseudo_id: str,
    cohort_id: str,
    case_id: str,
    case_version: str,
    grade: CaseGrade,
    blueprint: GradingBlueprint,
    legacy_trace: LegacyPhysioTrace,
    turns: list[Turn],
    confirmation: ConfirmationState = ConfirmationState.UNCONFIRMED,
    blueprint_content_hash: str | None = None,
    blueprint_source: str | None = None,
    tenant_id: str | None = None,
    authoring_session: DbSession | None = None,
) -> str:
    """
    Build and append a graded session record. Returns replay_hash.

    When ``blueprint_content_hash`` is set, finalize verifies it against
    ``published_case_versions`` (tenant-scoped) before any ledger write.
    Legacy null hashes skip verification.
    """
    validate_blueprint_provenance(blueprint_content_hash, blueprint_source)
    if blueprint_content_hash is not None:
        if tenant_id is None or authoring_session is None:
            raise ValueError(
                "tenant_id and authoring_session are required when "
                "blueprint_content_hash is stamped"
            )
        from services.pratibimb.ledger.blueprint_hash import (
            verify_blueprint_hash_at_finalize,
        )

        verify_blueprint_hash_at_finalize(
            authoring_session,
            session_id=session_id,
            tenant_id=tenant_id,
            case_id=case_id,
            case_version=case_version,
            stamped_hash=blueprint_content_hash,
        )

    record = build_record(
        session_id=session_id,
        learner_pseudo_id=learner_pseudo_id,
        cohort_id=cohort_id,
        case_id=case_id,
        case_version=case_version,
        grade=grade,
        blueprint=blueprint,
        legacy_trace=legacy_trace,
        turns=turns,
        confirmation=confirmation,
        blueprint_content_hash=blueprint_content_hash,
        blueprint_source=blueprint_source,
    )
    replay_hash = SqlLedgerWriter(db).append(record)
    if tenant_id is not None:
        from services.pratibimb.ledger.evidence_projector import EvidenceProjector

        EvidenceProjector(db, db).project_one(session_id, tenant_id=tenant_id)
    return replay_hash
