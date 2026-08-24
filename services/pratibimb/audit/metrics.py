"""Prometheus-compatible audit sink metrics (in-process; scrape via /metrics)."""
from __future__ import annotations

import threading
from collections import defaultdict


class _LabeledCounter:
    """Minimal Counter with .labels(...).inc() API."""

    __slots__ = ("name", "_values", "_lock", "_label_items")

    def __init__(self, name: str) -> None:
        self.name = name
        self._values: dict[tuple[tuple[str, str], ...], int] = defaultdict(int)
        self._lock = threading.Lock()
        self._label_items: tuple[tuple[str, str], ...] = ()

    def labels(self, **kwargs: str) -> _LabeledCounter:
        labeled = _LabeledCounter(self.name)
        labeled._values = self._values
        labeled._lock = self._lock
        labeled._label_items = tuple(sorted(kwargs.items()))
        return labeled

    def inc(self, amount: int = 1) -> None:
        with self._lock:
            self._values[self._label_items] += amount

    def total(self) -> int:
        with self._lock:
            return sum(self._values.values())

    def collect(self) -> list[tuple[tuple[tuple[str, str], ...], int]]:
        with self._lock:
            return list(self._values.items())


class _LabeledGauge:
    """Minimal Gauge with .labels(...).set() API."""

    __slots__ = ("name", "_values", "_lock", "_label_items")

    def __init__(self, name: str) -> None:
        self.name = name
        self._values: dict[tuple[tuple[str, str], ...], float] = {}
        self._lock = threading.Lock()
        self._label_items: tuple[tuple[str, str], ...] = ()

    def labels(self, **kwargs: str) -> _LabeledGauge:
        labeled = _LabeledGauge(self.name)
        labeled._values = self._values
        labeled._lock = self._lock
        labeled._label_items = tuple(sorted(kwargs.items()))
        return labeled

    def set(self, value: float) -> None:
        with self._lock:
            self._values[self._label_items] = value

    def collect(self) -> list[tuple[tuple[tuple[str, str], ...], float]]:
        with self._lock:
            return list(self._values.items())


# Label cardinality notes (read before adding labels):
#   sink    — low cardinality: primary | fallback
#   outcome — low cardinality: ok | error
#   tenant  — bounded by institution count; expected O(10²) at launch (≈100–300).
#             If tenant count exceeds ~1000, bucket tenant into tenant_size_class
#             (small|medium|large) instead of raw tenant_id. Keep sink/outcome exact.

AUDIT_SINK_EMIT_TOTAL = _LabeledCounter("audit_sink_emit_total")
AUDIT_SINK_FAILURE_TOTAL = _LabeledCounter("audit_sink_failure_total")
STATUS_LIST_COMPAT_COERCION_TOTAL = _LabeledCounter("status_list_compat_coercion_total")
AUDIT_PRIMARY_FAILURES_PER_MINUTE = _LabeledGauge(
    "audit_primary_sink_failures_per_minute"
)
# tenant — same cardinality bounds as counters above; zero when fallback is empty.
AUDIT_FALLBACK_DRAIN_LAG_SECONDS = _LabeledGauge(
    "audit_fallback_drain_lag_seconds"
)


def _format_labels(label_items: tuple[tuple[str, str], ...]) -> str:
    if not label_items:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in label_items)
    return "{" + inner + "}"


def render_prometheus_metrics() -> str:
    """Render all audit metrics in Prometheus text exposition format."""
    lines: list[str] = []

    for name, metric in (
        ("audit_sink_emit_total", AUDIT_SINK_EMIT_TOTAL),
        ("audit_sink_failure_total", AUDIT_SINK_FAILURE_TOTAL),
        ("status_list_compat_coercion_total", STATUS_LIST_COMPAT_COERCION_TOTAL),
    ):
        lines.append(f"# TYPE {name} counter")
        for label_items, value in metric.collect():
            if value:
                lines.append(f"{name}{_format_labels(label_items)} {value}")

    name = "audit_primary_sink_failures_per_minute"
    lines.append(f"# TYPE {name} gauge")
    for label_items, value in AUDIT_PRIMARY_FAILURES_PER_MINUTE.collect():
        lines.append(f"{name}{_format_labels(label_items)} {value}")

    name = "audit_fallback_drain_lag_seconds"
    lines.append(f"# TYPE {name} gauge")
    for label_items, value in AUDIT_FALLBACK_DRAIN_LAG_SECONDS.collect():
        lines.append(f"{name}{_format_labels(label_items)} {value}")

    from services.pratibimb.ledger_read.catalog_metrics import (
        render_catalog_prometheus_metrics,
    )

    catalog_block = render_catalog_prometheus_metrics().strip()
    if catalog_block:
        lines.extend(catalog_block.split("\n"))

    from services.pratibimb.ledger_read.ncvet_metrics import render_ncvet_prometheus_metrics

    ncvet_block = render_ncvet_prometheus_metrics().strip()
    if ncvet_block:
        lines.extend(ncvet_block.split("\n"))

    return "\n".join(line for line in lines if line) + "\n"
