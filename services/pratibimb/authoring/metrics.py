"""Prometheus-compatible authoring publish/retire metrics."""
from __future__ import annotations

from services.pratibimb.audit.metrics import _LabeledCounter, _LabeledGauge, _format_labels

# Label cardinality: tenant O(10²); tier / reason_code are closed enums.
AUTHORING_PUBLISH_TOTAL = _LabeledCounter("authoring_publish_total")
AUTHORING_RETIRE_TOTAL = _LabeledCounter("authoring_retire_total")
AUTHORING_PUBLISHED_ACTIVE_TOTAL = _LabeledGauge("authoring_published_active_total")
AUTHORING_RETIRED_TOTAL = _LabeledGauge("authoring_retired_total")
# E2.2: corpus projection (result includes skipped in E2.2.b)
AUTHORING_CORPUS_PROJECTION_TOTAL = _LabeledCounter("authoring_corpus_projection_total")
AUTHORING_CORPUS_PROJECTION_STALE = _LabeledGauge("authoring_corpus_projection_stale")
# E2.2.d: sync heal on explicit-pick corpus miss (distinct from hook/reconcile)
AUTHORING_CORPUS_PROJECT_ON_MISS_TOTAL = _LabeledCounter(
    "authoring_corpus_project_on_miss_total"
)
# E2.3: reconciler ops surface
# reason ∈ {startup, periodic, manual}; outcome ∈ {success, skipped, error}
RECONCILER_REASONS = frozenset({"startup", "periodic", "manual"})
RECONCILER_OUTCOMES = frozenset({"success", "skipped", "error"})
RECONCILER_PASS_OUTCOMES = frozenset({"ok", "error"})
CORPUS_RECONCILER_ROW_TOTAL = _LabeledCounter("corpus_reconciler_row_total")
CORPUS_RECONCILER_PASS_TOTAL = _LabeledCounter("corpus_reconciler_pass_total")
CORPUS_RECONCILER_LAG_SECONDS = _LabeledGauge("corpus_reconciler_lag_seconds")
# E2.4.b: singular legacy hatch (SessionManager pick with no corpus_session).
# Sole call site: SessionManager._load_published_pick hatch branch.
# Should trend toward zero; climbing ⇒ a new caller was added against I-E24-3.
LEDGER_LEGACY_HATCH_TOTAL = _LabeledCounter("ledger_legacy_hatch_total")


def record_publish(*, tenant_id: str, tier: str) -> None:
    AUTHORING_PUBLISH_TOTAL.labels(tenant=tenant_id, tier=tier).inc()


def record_retire(*, tenant_id: str, reason_code: str) -> None:
    AUTHORING_RETIRE_TOTAL.labels(tenant=tenant_id, reason_code=reason_code).inc()


def set_published_active(*, tenant_id: str, value: int) -> None:
    AUTHORING_PUBLISHED_ACTIVE_TOTAL.labels(tenant=tenant_id).set(float(value))


def set_retired_total(*, tenant_id: str, value: int) -> None:
    AUTHORING_RETIRED_TOTAL.labels(tenant=tenant_id).set(float(value))


def record_corpus_projection(*, result: str, tenant_id: str) -> None:
    AUTHORING_CORPUS_PROJECTION_TOTAL.labels(result=result, tenant=tenant_id).inc()


def set_corpus_projection_stale(*, tenant_id: str) -> None:
    AUTHORING_CORPUS_PROJECTION_STALE.labels(tenant=tenant_id).set(1.0)


def clear_corpus_projection_stale(*, tenant_id: str) -> None:
    AUTHORING_CORPUS_PROJECTION_STALE.labels(tenant=tenant_id).set(0.0)


def record_corpus_project_on_miss(*, outcome: str, tenant_id: str) -> None:
    """outcome: projected | not_found"""
    AUTHORING_CORPUS_PROJECT_ON_MISS_TOTAL.labels(
        outcome=outcome, tenant=tenant_id
    ).inc()


def record_corpus_reconciler_row(
    *, tenant_id: str, reason: str, outcome: str
) -> None:
    """
    One increment per published row examined in a reconcile pass.

    reason: startup|periodic|manual; outcome: success|skipped|error.
    Unknown labels raise — Prometheus has no CHECK constraint.
    """
    if reason not in RECONCILER_REASONS:
        raise ValueError(
            f"invalid reconciler reason {reason!r}; "
            f"expected one of {sorted(RECONCILER_REASONS)}"
        )
    if outcome not in RECONCILER_OUTCOMES:
        raise ValueError(
            f"invalid reconciler outcome {outcome!r}; "
            f"expected one of {sorted(RECONCILER_OUTCOMES)}"
        )
    CORPUS_RECONCILER_ROW_TOTAL.labels(
        tenant=tenant_id, reason=reason, outcome=outcome
    ).inc()


def set_corpus_reconciler_lag_seconds(*, tenant_id: str, seconds: float) -> None:
    """Oldest-stale age in seconds; 0 when no stale rows."""
    CORPUS_RECONCILER_LAG_SECONDS.labels(tenant=tenant_id).set(float(seconds))


def record_corpus_reconciler_pass(
    *, tenant_id: str, reason: str, outcome: str
) -> None:
    """
    One increment per tenant pass in a reconciler sweep.

    outcome: ok|error — pass-level (tenant call completed vs raised).
    Distinct from row_total so a raise-before-rows does not invent fake row errors.
    """
    if reason not in RECONCILER_REASONS:
        raise ValueError(
            f"invalid reconciler reason {reason!r}; "
            f"expected one of {sorted(RECONCILER_REASONS)}"
        )
    if outcome not in RECONCILER_PASS_OUTCOMES:
        raise ValueError(
            f"invalid reconciler pass outcome {outcome!r}; "
            f"expected one of {sorted(RECONCILER_PASS_OUTCOMES)}"
        )
    CORPUS_RECONCILER_PASS_TOTAL.labels(
        tenant=tenant_id, reason=reason, outcome=outcome
    ).inc()


def record_ledger_legacy_hatch(*, tenant_id: str) -> None:
    """
    Increment when SessionManager takes the no-corpus_session pick hatch.

    Call site: ``SessionManager._load_published_pick`` (legacy hatch branch).
    Series: ``ledger_legacy_hatch_total{tenant}``. Do not add a reason label —
    the hatch is singular (I-E24-3).
    """
    LEDGER_LEGACY_HATCH_TOTAL.labels(tenant=tenant_id).inc()


def render_authoring_prometheus_metrics() -> str:
    lines: list[str] = []

    for name, metric in (
        ("authoring_publish_total", AUTHORING_PUBLISH_TOTAL),
        ("authoring_retire_total", AUTHORING_RETIRE_TOTAL),
        ("authoring_corpus_projection_total", AUTHORING_CORPUS_PROJECTION_TOTAL),
        (
            "authoring_corpus_project_on_miss_total",
            AUTHORING_CORPUS_PROJECT_ON_MISS_TOTAL,
        ),
        ("corpus_reconciler_row_total", CORPUS_RECONCILER_ROW_TOTAL),
        ("corpus_reconciler_pass_total", CORPUS_RECONCILER_PASS_TOTAL),
        ("ledger_legacy_hatch_total", LEDGER_LEGACY_HATCH_TOTAL),
    ):
        lines.append(f"# TYPE {name} counter")
        for label_items, value in metric.collect():
            if value:
                lines.append(f"{name}{_format_labels(label_items)} {value}")

    for name, metric in (
        ("authoring_published_active_total", AUTHORING_PUBLISHED_ACTIVE_TOTAL),
        ("authoring_retired_total", AUTHORING_RETIRED_TOTAL),
        ("authoring_corpus_projection_stale", AUTHORING_CORPUS_PROJECTION_STALE),
        ("corpus_reconciler_lag_seconds", CORPUS_RECONCILER_LAG_SECONDS),
    ):
        lines.append(f"# TYPE {name} gauge")
        for label_items, value in metric.collect():
            lines.append(f"{name}{_format_labels(label_items)} {value}")

    return "\n".join(lines) + ("\n" if lines else "")


def reset_authoring_metrics_for_tests() -> None:
    """Clear in-process counters/gauges between tests."""
    for metric in (
        AUTHORING_PUBLISH_TOTAL,
        AUTHORING_RETIRE_TOTAL,
        AUTHORING_CORPUS_PROJECTION_TOTAL,
        AUTHORING_CORPUS_PROJECT_ON_MISS_TOTAL,
        CORPUS_RECONCILER_ROW_TOTAL,
        CORPUS_RECONCILER_PASS_TOTAL,
        LEDGER_LEGACY_HATCH_TOTAL,
    ):
        with metric._lock:
            metric._values.clear()
    for metric in (
        AUTHORING_PUBLISHED_ACTIVE_TOTAL,
        AUTHORING_RETIRED_TOTAL,
        AUTHORING_CORPUS_PROJECTION_STALE,
        CORPUS_RECONCILER_LAG_SECONDS,
    ):
        with metric._lock:
            metric._values.clear()
