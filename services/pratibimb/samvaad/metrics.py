"""Bounded-cardinality SAMVAAD Prometheus metrics."""
from __future__ import annotations

from services.pratibimb.audit.metrics import _LabeledCounter, _LabeledGauge, _format_labels

SUMMATIVE_REJECTED_COUNTER = "samvaad_summative_rejected_total"
REASON_PATIENT_REPORTED = "patient_reported"

ASSESSMENT_KINDS = frozenset({"summative", "formative"})
EVIDENCE_CLASSES = frozenset(
    {"machine_sim", "preceptor_attested", "patient_reported"}
)
OUTCOMES = frozenset({"success", "skipped", "error"})
REJECTION_REASONS = frozenset({REASON_PATIENT_REPORTED})

SAMVAAD_EVIDENCE_INSERT_TOTAL = _LabeledCounter("samvaad_evidence_insert_total")
SAMVAAD_DHAARA_PUBLISH_TOTAL = _LabeledCounter("samvaad_dhaara_publish_total")
SAMVAAD_DHAARA_RECONCILE_TOTAL = _LabeledCounter("samvaad_dhaara_reconcile_total")
SAMVAAD_DHAARA_RECONCILE_LAST_SUCCESS = _LabeledGauge(
    "samvaad_dhaara_reconcile_last_success_timestamp_seconds"
)
SAMVAAD_DHAARA_PROJECTION_STALE = _LabeledGauge(
    "samvaad_dhaara_projection_stale_seconds"
)
SAMVAAD_SUMMATIVE_REJECTED_TOTAL = _LabeledCounter(SUMMATIVE_REJECTED_COUNTER)


def _assert_label(value: str, allowed: frozenset[str], name: str) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {name} {value!r}; expected one of {sorted(allowed)}")


def record_summative_rejected(*, reason: str) -> None:
    _assert_label(reason, REJECTION_REASONS, "reason")
    SAMVAAD_SUMMATIVE_REJECTED_TOTAL.labels(reason=reason).inc()


def summative_rejected_total(*, reason: str) -> int:
    _assert_label(reason, REJECTION_REASONS, "reason")
    labels = tuple(sorted({"reason": reason}.items()))
    return dict(SAMVAAD_SUMMATIVE_REJECTED_TOTAL.collect()).get(labels, 0)


def record_evidence_insert(
    *, assessment_kind: str, evidence_class: str, outcome: str
) -> None:
    _assert_label(assessment_kind, ASSESSMENT_KINDS, "assessment_kind")
    _assert_label(evidence_class, EVIDENCE_CLASSES, "evidence_class")
    _assert_label(outcome, OUTCOMES, "outcome")
    SAMVAAD_EVIDENCE_INSERT_TOTAL.labels(
        assessment_kind=assessment_kind,
        evidence_class=evidence_class,
        outcome=outcome,
    ).inc()


def record_dhaara_publish(
    *, assessment_kind: str, evidence_class: str, outcome: str
) -> None:
    _assert_label(assessment_kind, ASSESSMENT_KINDS, "assessment_kind")
    _assert_label(evidence_class, EVIDENCE_CLASSES, "evidence_class")
    _assert_label(outcome, OUTCOMES, "outcome")
    SAMVAAD_DHAARA_PUBLISH_TOTAL.labels(
        assessment_kind=assessment_kind,
        evidence_class=evidence_class,
        outcome=outcome,
    ).inc()


def record_dhaara_reconcile(*, assessment_kind: str, outcome: str) -> None:
    _assert_label(assessment_kind, ASSESSMENT_KINDS, "assessment_kind")
    _assert_label(outcome, OUTCOMES, "outcome")
    SAMVAAD_DHAARA_RECONCILE_TOTAL.labels(
        assessment_kind=assessment_kind,
        outcome=outcome,
    ).inc()


def set_dhaara_reconcile_success(
    *, assessment_kind: str, timestamp_seconds: float
) -> None:
    _assert_label(assessment_kind, ASSESSMENT_KINDS, "assessment_kind")
    SAMVAAD_DHAARA_RECONCILE_LAST_SUCCESS.labels(
        assessment_kind=assessment_kind
    ).set(timestamp_seconds)


def set_dhaara_stale_seconds(*, assessment_kind: str, seconds: float) -> None:
    _assert_label(assessment_kind, ASSESSMENT_KINDS, "assessment_kind")
    SAMVAAD_DHAARA_PROJECTION_STALE.labels(
        assessment_kind=assessment_kind
    ).set(max(0.0, seconds))


def render_samvaad_prometheus_metrics() -> str:
    lines: list[str] = []
    for name, metric in (
        ("samvaad_evidence_insert_total", SAMVAAD_EVIDENCE_INSERT_TOTAL),
        ("samvaad_dhaara_publish_total", SAMVAAD_DHAARA_PUBLISH_TOTAL),
        ("samvaad_dhaara_reconcile_total", SAMVAAD_DHAARA_RECONCILE_TOTAL),
        (SUMMATIVE_REJECTED_COUNTER, SAMVAAD_SUMMATIVE_REJECTED_TOTAL),
    ):
        lines.append(f"# TYPE {name} counter")
        for labels, value in metric.collect():
            if value:
                lines.append(f"{name}{_format_labels(labels)} {value}")
    for name, metric in (
        (
            "samvaad_dhaara_reconcile_last_success_timestamp_seconds",
            SAMVAAD_DHAARA_RECONCILE_LAST_SUCCESS,
        ),
        (
            "samvaad_dhaara_projection_stale_seconds",
            SAMVAAD_DHAARA_PROJECTION_STALE,
        ),
    ):
        lines.append(f"# TYPE {name} gauge")
        for labels, value in metric.collect():
            lines.append(f"{name}{_format_labels(labels)} {value}")
    return "\n".join(lines) + ("\n" if lines else "")


def reset_summative_metrics() -> None:
    reset_samvaad_metrics_for_tests()


def reset_samvaad_metrics_for_tests() -> None:
    for metric in (
        SAMVAAD_EVIDENCE_INSERT_TOTAL,
        SAMVAAD_DHAARA_PUBLISH_TOTAL,
        SAMVAAD_DHAARA_RECONCILE_TOTAL,
        SAMVAAD_SUMMATIVE_REJECTED_TOTAL,
        SAMVAAD_DHAARA_RECONCILE_LAST_SUCCESS,
        SAMVAAD_DHAARA_PROJECTION_STALE,
    ):
        with metric._lock:
            metric._values.clear()
