"""
E2.2: post-commit corpus projection façade + reconcile safety net.
E2.3.a: reconciler lag + reason-labeled counters (no scheduler yet).

Call only after authoring publish/retire has committed (hook path), or from
reconcile. Never raises — projection is eventual; reconcile heals failures.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.authoring.metrics import (
    clear_corpus_projection_stale,
    record_corpus_projection,
    record_corpus_reconciler_row,
    set_corpus_projection_stale,
    set_corpus_reconciler_lag_seconds,
)
from services.pratibimb.authoring.models import PublishedCaseVersionRow
from services.pratibimb.ledger.corpus_projector import (
    CorpusProjector,
    derive_retire_flags,
)
from services.pratibimb.ledger.db import ensure_runtime_namespace, get_engine
from services.pratibimb.ledger.models import Base, PublishedCaseCorpusRow
from shared.logging import get_logger

log = get_logger(__name__)

ProjectionResult = Literal["success", "error", "skipped"]
ReconcileReason = Literal["startup", "periodic", "manual"]


def _open_corpus_session() -> Session:
    engine = get_engine()
    ensure_runtime_namespace(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def _is_projection_current(
    published: PublishedCaseVersionRow,
    row: PublishedCaseCorpusRow | None,
) -> bool:
    """True when corpus row already matches published identity + derived retire flags."""
    if row is None:
        return False
    workflow_state, probe_only = derive_retire_flags(published)
    return (
        row.content_hash == published.content_hash
        and bool(row.probe_only) is probe_only
        and row.workflow_state == workflow_state
        and row.tenant_id == published.tenant_id
        and row.case_id == published.case_id
        and row.version == published.version
        and row.assessment_mode == published.assessment_mode
        and row.harness_version == published.harness_version
    )


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_reconciler_lag_seconds(
    published_rows: Sequence[PublishedCaseVersionRow],
    corpus_by_id: Mapping[str, PublishedCaseCorpusRow | None],
    *,
    now: datetime,
) -> float:
    """
    Oldest-stale lag for a tenant.

    ``lag = now − min(published_at)`` over rows failing ``_is_projection_current``.
    Returns ``0.0`` when no stale rows exist.
    """
    now_utc = _aware_utc(now)
    oldest: datetime | None = None
    for published in published_rows:
        corpus_row = corpus_by_id.get(published.id)
        if _is_projection_current(published, corpus_row):
            continue
        published_at = _aware_utc(published.published_at)
        if oldest is None or published_at < oldest:
            oldest = published_at
    if oldest is None:
        return 0.0
    return max(0.0, (now_utc - oldest).total_seconds())


def project_after_authoring_commit(
    published_id: str,
    *,
    authoring_session: Session,
    corpus_session: Session | None = None,
    tenant_id: str | None = None,
    idempotent: bool = False,
) -> ProjectionResult:
    """
    Project one published row into ``runtime.published_case_corpus``.

    I-E22-1 — Authoring commit ⊥ projection outcome.
    Call this ONLY after ``session.commit()`` for publish/retire has succeeded
    (hook path: ``idempotent=False``), or from reconcile (``idempotent=True``).
    This function must NEVER raise into the HTTP handler: catching and
    swallowing here is intentional. Surfacing the error would turn publish
    into a two-phase commit against a derived cache; the instinct to "fail
    loud to the client" is wrong for this seam. Log + metrics make the
    failure loud to operators; reconcile makes it eventual, not silent.

    I-E22-2 — Decision-idempotent reconcile.
    When ``idempotent=True`` and the corpus row is already current, return
    ``"skipped"`` without writing and without emitting ``success``.

    Stale gauge (success / skipped / error):
      - ``success`` → clear stale
      - ``skipped`` → **no-op** on the gauge (hook already cleared; do not
        emit a spurious stale→clear transition)
      - ``error`` → set stale
    """
    own = corpus_session is None
    sess = corpus_session if corpus_session is not None else _open_corpus_session()
    tenant_label = tenant_id or "unknown"
    try:
        try:
            published = authoring_session.get(PublishedCaseVersionRow, published_id)
            if published is None:
                raise KeyError(f"unknown published_case_version {published_id}")
            if tenant_id is None:
                tenant_label = published.tenant_id

            if idempotent:
                existing = sess.get(PublishedCaseCorpusRow, published_id)
                if _is_projection_current(published, existing):
                    # I-E22-2: decide not to project — metric skipped, gauge untouched.
                    record_corpus_projection(result="skipped", tenant_id=tenant_label)
                    return "skipped"

            CorpusProjector(authoring_session, sess).project_one(published_id)
            sess.commit()
            record_corpus_projection(result="success", tenant_id=tenant_label)
            clear_corpus_projection_stale(tenant_id=tenant_label)
            return "success"
        except Exception:
            # I-E22-1: do not re-raise. Authoring is already committed (hook path).
            log.exception(
                "corpus projection failed "
                "(published_id=%s tenant=%s idempotent=%s); "
                "publish/retire HTTP stays success when on hook path; "
                "reconcile will heal",
                published_id,
                tenant_label,
                idempotent,
            )
            try:
                sess.rollback()
            except Exception:
                log.exception("corpus session rollback failed after projection error")
            record_corpus_projection(result="error", tenant_id=tenant_label)
            set_corpus_projection_stale(tenant_id=tenant_label)
            return "error"
    finally:
        if own:
            sess.close()


def reconcile_tenant(
    tenant_id: str,
    *,
    authoring_session: Session,
    corpus_session: Session | None = None,
    reason: ReconcileReason = "manual",
    now: datetime | None = None,
) -> dict[str, int]:
    """
    Safety-net reconcile for one tenant (I-E22-2 / E2.3.a).

    Calls the same façade with ``idempotent=True`` so a prior successful hook
    yields ``skipped``, not a second ``success``.

    ``reason`` labels reconciler counters (startup|periodic|manual). Default
    ``manual`` keeps tests/ops shell off the scheduler series. After the pass,
    sets ``corpus_reconciler_lag_seconds`` to oldest-stale age (0 if current).
    """
    own = corpus_session is None
    sess = corpus_session if corpus_session is not None else _open_corpus_session()
    counts = {"success": 0, "skipped": 0, "error": 0}
    try:
        published_rows = list(
            authoring_session.scalars(
                select(PublishedCaseVersionRow).where(
                    PublishedCaseVersionRow.tenant_id == tenant_id
                )
            )
        )
        for published in published_rows:
            result = project_after_authoring_commit(
                published.id,
                authoring_session=authoring_session,
                corpus_session=sess,
                tenant_id=tenant_id,
                idempotent=True,
            )
            counts[result] += 1
            record_corpus_reconciler_row(
                tenant_id=tenant_id, reason=reason, outcome=result
            )

        corpus_by_id: dict[str, PublishedCaseCorpusRow | None] = {
            p.id: sess.get(PublishedCaseCorpusRow, p.id) for p in published_rows
        }
        clock = now if now is not None else datetime.now(timezone.utc)
        lag = compute_reconciler_lag_seconds(
            published_rows,
            corpus_by_id,
            now=clock,
        )
        set_corpus_reconciler_lag_seconds(tenant_id=tenant_id, seconds=lag)
        return counts
    finally:
        if own:
            sess.close()
