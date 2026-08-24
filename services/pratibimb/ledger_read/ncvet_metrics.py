"""F.b NCVET read Prometheus counters — prefix matches ``ledger_read_audit`` table.

I-E3-10 enforcement (NCVET): ``record_ncvet_audit_metrics`` reachable only from
``ncvet_safe_emit`` in ``ncvet_audit.py`` (option a). Belt:
``test_i_e3_10_single_call_site_for_record_ncvet_audit_metrics`` in
``test_authoring_f_c_a.py`` (option b).
"""
from __future__ import annotations

from services.pratibimb.audit.metrics import _LabeledCounter
from services.pratibimb.audit.sink import AuditEvent

LEDGER_READ_NCVET_TOTAL = _LabeledCounter("ledger_read_ncvet_total")

LEDGER_READ_NCVET_ROWS_RETURNED_TOTAL = _LabeledCounter(
    "ledger_read_ncvet_rows_returned_total"
)

ERROR_KIND_NONE = ""

KNOWN_NCVET_QUERY_KINDS = frozenset({
    "get_session_evidence",
    "list_learner_sessions",
})

KNOWN_NCVET_ERROR_KINDS = frozenset({
    ERROR_KIND_NONE,
    "consent_scope",
    "tenant_mismatch",
    "cursor_invalid",
    "not_found",
    "unexpected",
})


class NcvetMetricsError(ValueError):
    """Invalid NCVET metric labels — fail loud at increment time."""


def _normalized_error_kind(event: AuditEvent) -> str:
    if event.outcome == "ok":
        return ERROR_KIND_NONE
    if event.error_kind:
        return event.error_kind
    return "unexpected"


def record_ncvet_audit_metrics(
    event: AuditEvent,
    *,
    payload_row_count: int = 0,
) -> None:
    """Increment NCVET counters — call only from ``ncvet_safe_emit``."""
    if event.query_kind not in KNOWN_NCVET_QUERY_KINDS:
        raise NcvetMetricsError(
            f"query_kind {event.query_kind!r} not in KNOWN_NCVET_QUERY_KINDS"
        )
    error_kind = _normalized_error_kind(event)
    if error_kind not in KNOWN_NCVET_ERROR_KINDS:
        raise NcvetMetricsError(
            f"error_kind {error_kind!r} not in KNOWN_NCVET_ERROR_KINDS"
        )
    if event.outcome not in {"ok", "scope_denied", "error"}:
        raise NcvetMetricsError(f"unexpected outcome {event.outcome!r}")

    LEDGER_READ_NCVET_TOTAL.labels(
        tenant_id=event.tenant_id,
        query_kind=event.query_kind,
        outcome=event.outcome,
        error_kind=error_kind,
    ).inc()

    if event.outcome == "ok" and payload_row_count > 0:
        LEDGER_READ_NCVET_ROWS_RETURNED_TOTAL.labels(
            tenant_id=event.tenant_id,
            query_kind=event.query_kind,
        ).inc(payload_row_count)


def render_ncvet_prometheus_metrics() -> str:
    from services.pratibimb.audit.metrics import _format_labels

    lines: list[str] = []
    for name, metric in (
        ("ledger_read_ncvet_total", LEDGER_READ_NCVET_TOTAL),
        ("ledger_read_ncvet_rows_returned_total", LEDGER_READ_NCVET_ROWS_RETURNED_TOTAL),
    ):
        lines.append(f"# TYPE {name} counter")
        for label_items, value in metric.collect():
            if value:
                lines.append(f"{name}{_format_labels(label_items)} {value}")
    return "\n".join(lines) + ("\n" if lines else "")


def reset_ncvet_metrics_for_tests() -> None:
    LEDGER_READ_NCVET_TOTAL._values.clear()  # type: ignore[attr-defined]
    LEDGER_READ_NCVET_ROWS_RETURNED_TOTAL._values.clear()  # type: ignore[attr-defined]
