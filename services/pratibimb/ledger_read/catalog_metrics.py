"""E3.c catalog read Prometheus counters — prefix matches ``ledger_read_audit`` table.

I-E3-10 enforcement: ``record_catalog_audit_metrics`` is reachable only from
``catalog_safe_emit`` in ``catalog_audit.py`` (option a). A grep test in
``test_authoring_e3_c_a.py`` asserts no other call sites (option b belt).
"""
from __future__ import annotations

from services.pratibimb.audit.metrics import _LabeledCounter
from services.pratibimb.audit.sink import AuditEvent

# Prefix matches ledger_read_audit table; do not rename without renaming the audit surface.
LEDGER_READ_CATALOG_TOTAL = _LabeledCounter("ledger_read_catalog_total")

# Increments with rows in the response payload, after all authorization and redaction
# filters. If payload count diverges from DB match count, DB count is a separate concern.
LEDGER_READ_CATALOG_ROWS_RETURNED_TOTAL = _LabeledCounter(
    "ledger_read_catalog_rows_returned_total"
)

ERROR_KIND_NONE = ""

KNOWN_CATALOG_QUERY_KINDS = frozenset({
    "get_published_case_versions",
    "get_retirement_history",
})

KNOWN_CATALOG_ERROR_KINDS = frozenset({
    ERROR_KIND_NONE,
    "consent_scope",
    "tenant_mismatch",
    "cursor_invalid",
    "unexpected",
})


class CatalogMetricsError(ValueError):
    """Invalid catalog metric labels — fail loud at registration/increment time."""


def _normalized_error_kind(event: AuditEvent) -> str:
    if event.outcome == "ok":
        return ERROR_KIND_NONE
    if event.error_kind:
        return event.error_kind
    return "unexpected"


def record_catalog_audit_metrics(
    event: AuditEvent,
    *,
    payload_row_count: int = 0,
) -> None:
    """Increment catalog counters — I-E3-10: call only from ``catalog_safe_emit``."""
    if event.query_kind not in KNOWN_CATALOG_QUERY_KINDS:
        raise CatalogMetricsError(
            f"query_kind {event.query_kind!r} not in KNOWN_CATALOG_QUERY_KINDS"
        )
    error_kind = _normalized_error_kind(event)
    if error_kind not in KNOWN_CATALOG_ERROR_KINDS:
        raise CatalogMetricsError(
            f"error_kind {error_kind!r} not in KNOWN_CATALOG_ERROR_KINDS"
        )
    if event.outcome not in {"ok", "scope_denied", "error"}:
        raise CatalogMetricsError(f"unexpected outcome {event.outcome!r}")

    LEDGER_READ_CATALOG_TOTAL.labels(
        tenant_id=event.tenant_id,
        query_kind=event.query_kind,
        outcome=event.outcome,
        error_kind=error_kind,
    ).inc()

    if event.outcome == "ok" and payload_row_count > 0:
        LEDGER_READ_CATALOG_ROWS_RETURNED_TOTAL.labels(
            tenant_id=event.tenant_id,
            query_kind=event.query_kind,
        ).inc(payload_row_count)


def render_catalog_prometheus_metrics() -> str:
    from services.pratibimb.audit.metrics import _format_labels

    lines: list[str] = []
    for name, metric in (
        ("ledger_read_catalog_total", LEDGER_READ_CATALOG_TOTAL),
        ("ledger_read_catalog_rows_returned_total", LEDGER_READ_CATALOG_ROWS_RETURNED_TOTAL),
    ):
        lines.append(f"# TYPE {name} counter")
        for label_items, value in metric.collect():
            if value:
                lines.append(f"{name}{_format_labels(label_items)} {value}")
    return "\n".join(lines) + ("\n" if lines else "")


def reset_catalog_metrics_for_tests() -> None:
    """Test helper — clear in-process catalog counters."""
    LEDGER_READ_CATALOG_TOTAL._values.clear()  # type: ignore[attr-defined]
    LEDGER_READ_CATALOG_ROWS_RETURNED_TOTAL._values.clear()  # type: ignore[attr-defined]
