"""E3.c.a: catalog read Prometheus counters (I-E3-10…14)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
    "services.pratibimb.tests.test_authoring_e3_b1",
]

from services.pratibimb.audit.metrics import (
    AUDIT_SINK_FAILURE_TOTAL,
    render_prometheus_metrics,
)
from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink
from services.pratibimb.ledger_read.catalog_audit import AuditingAuthoringCatalogReadService
from services.pratibimb.ledger_read.catalog_metrics import (
    ERROR_KIND_NONE,
    CatalogMetricsError,
    record_catalog_audit_metrics,
    reset_catalog_metrics_for_tests,
)
from services.pratibimb.ledger_read.deps import reset_auth_wiring_cache
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_e3_b1 import (
    PUBLISHED_ONLY,
    RETIREMENT_READER,
    _patch_audit_sink,
)
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    CATALOG_READER,
    OTHER_TENANT,
    _dev_auth,
    _http_publish_retire,
    slice4_client,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_E3_C_A_TOUCHED = (
    "services/pratibimb/ledger_read/catalog_metrics.py",
    "services/pratibimb/ledger_read/catalog_audit.py",
    "services/pratibimb/audit/metrics.py",
    "services/pratibimb/tests/test_authoring_e3_c_a.py",
)


def _reset_metrics_for_tests() -> None:
    AUDIT_SINK_FAILURE_TOTAL._values.clear()  # type: ignore[attr-defined]
    reset_catalog_metrics_for_tests()


@pytest.fixture(autouse=True)
def _e3_c_a_metrics_reset():
    _reset_metrics_for_tests()
    yield
    _reset_metrics_for_tests()


def _metric_line(body: str, name: str, **labels: str) -> int:
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    needle = f"{name}{{{label_str}}} "
    for line in body.splitlines():
        if line.startswith(needle):
            return int(line.rsplit(" ", 1)[-1])
    return 0


def _catalog_total(body: str, *, query_kind: str, outcome: str, error_kind: str) -> int:
    return _metric_line(
        body,
        "ledger_read_catalog_total",
        tenant_id=TENANT,
        query_kind=query_kind,
        outcome=outcome,
        error_kind=error_kind,
    )


def _rows_returned_total(body: str, *, query_kind: str) -> int:
    return _metric_line(
        body,
        "ledger_read_catalog_rows_returned_total",
        tenant_id=TENANT,
        query_kind=query_kind,
    )


def test_i_e3_10_single_call_site_for_record_catalog_audit_metrics():
    """I-E3-10 option b: grep guard — exactly one increment call site in prod code."""
    call_sites: list[str] = []
    pratibimb = _REPO_ROOT / "services" / "pratibimb"
    for path in pratibimb.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("def record_catalog_audit_metrics"):
                continue
            if "record_catalog_audit_metrics(" not in line:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith(("import ", "from ")):
                continue
            call_sites.append(f"{rel}:{line_no}")
    assert call_sites == ["services/pratibimb/ledger_read/catalog_audit.py:99"]


def test_i_e3_10_scope_deny_audit_and_counter_in_sync(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    before = render_prometheus_metrics()
    resp = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1",
        headers=_dev_auth(PUBLISHED_ONLY),
    )
    assert resp.status_code == 403
    denied = [e for e in audit.events if e.query_kind == "get_retirement_history"]
    assert len(denied) == 1
    assert denied[0].error_kind == "consent_scope"
    after = render_prometheus_metrics()
    assert _catalog_total(
        after,
        query_kind="get_retirement_history",
        outcome="scope_denied",
        error_kind="consent_scope",
    ) - _catalog_total(
        before,
        query_kind="get_retirement_history",
        outcome="scope_denied",
        error_kind="consent_scope",
    ) == len(denied)


def test_i_e3_11_error_kinds_and_outcome_split_on_metrics(slice4_client, monkeypatch):
    """Three deny paths: scope_denied×2 + error for cursor_invalid (I-E3-11)."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)

    scope_resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1",
        headers=_dev_auth("no-scope-subject"),
    )
    assert scope_resp.status_code == 403

    tenant_resp = slice4_client.get(
        f"/v1/ledger/published_case_versions?case_id=C1&tenant_id={OTHER_TENANT}",
        headers=_dev_auth(CATALOG_READER),
    )
    assert tenant_resp.status_code == 403

    _http_publish_retire(slice4_client, version="2.0.0")
    first = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1&limit=1",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor is not None
    tampered = cursor[:-2] + ("AA" if cursor[-2:] != "AA" else "BB")
    cursor_resp = slice4_client.get(
        f"/v1/ledger/retirement_history?case_id=C1&cursor={quote(tampered, safe='')}",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert cursor_resp.status_code == 400

    body = render_prometheus_metrics()
    assert _catalog_total(
        body,
        query_kind="get_published_case_versions",
        outcome="scope_denied",
        error_kind="consent_scope",
    ) == 1
    assert _catalog_total(
        body,
        query_kind="get_published_case_versions",
        outcome="scope_denied",
        error_kind="tenant_mismatch",
    ) == 1
    assert _catalog_total(
        body,
        query_kind="get_retirement_history",
        outcome="error",
        error_kind="cursor_invalid",
    ) == 1
    assert _catalog_total(
        body,
        query_kind="get_retirement_history",
        outcome="scope_denied",
        error_kind="cursor_invalid",
    ) == 0
    assert len([e for e in audit.events if e.error_kind == "cursor_invalid"]) == 1


def test_i_e3_11_unknown_error_kind_raises():
    event = AuditEvent(
        query_id="q-metrics",
        at_utc=datetime(2026, 8, 22, tzinfo=timezone.utc),
        tenant_id=TENANT,
        subject_pseudo_id="*",
        caller_kind="authoring",
        scope="authoring:read_published",
        query_kind="get_published_case_versions",
        query_params_hash="abc",
        query_params_bytes=3,
        outcome="error",
        duration_ms=0,
        error_kind="rate_limited",
    )
    with pytest.raises(CatalogMetricsError, match="error_kind"):
        record_catalog_audit_metrics(event)


def test_ok_zero_row_increments_catalog_not_rows_returned(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    before = render_prometheus_metrics()
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=missing-case",
        headers=_dev_auth(CATALOG_READER),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    ok = [e for e in audit.events if e.query_kind == "get_published_case_versions"]
    assert ok[-1].outcome == "ok"
    assert ok[-1].result_row_count == 0
    after = render_prometheus_metrics()
    assert _catalog_total(
        after,
        query_kind="get_published_case_versions",
        outcome="ok",
        error_kind=ERROR_KIND_NONE,
    ) - _catalog_total(
        before,
        query_kind="get_published_case_versions",
        outcome="ok",
        error_kind=ERROR_KIND_NONE,
    ) == 1
    assert _rows_returned_total(after, query_kind="get_published_case_versions") == _rows_returned_total(
        before, query_kind="get_published_case_versions"
    )


def test_ok_with_data_increments_rows_returned(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    before = render_prometheus_metrics()
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1&include_retired=true",
        headers=_dev_auth(CATALOG_READER),
    )
    assert resp.status_code == 200
    item_count = len(resp.json()["items"])
    assert item_count >= 1
    ok = [e for e in audit.events if e.query_kind == "get_published_case_versions"]
    assert ok[-1].result_row_count == item_count
    after = render_prometheus_metrics()
    assert _rows_returned_total(after, query_kind="get_published_case_versions") - _rows_returned_total(
        before, query_kind="get_published_case_versions"
    ) == item_count


def test_i_e3_12_fail_closed_no_catalog_ok_and_yes_sink_failure(slice4_client, monkeypatch):
    from services.pratibimb.tests.test_authoring_e3_b1 import _TrackingFailingSink
    from services.pratibimb.ledger_read import deps as ledger_deps
    import services.pratibimb.ledger_read.routes as ledger_routes

    ok_sink = InMemoryAuditSink()
    fail_sink = _TrackingFailingSink()
    state = {"sink": ok_sink}

    def _rotating_sink():
        return state["sink"]

    ledger_deps.get_audit_sink.cache_clear()
    monkeypatch.setattr(ledger_deps, "get_audit_sink", _rotating_sink)
    monkeypatch.setattr(ledger_routes, "get_audit_sink", _rotating_sink)
    reset_auth_wiring_cache()

    for version in ("1.0.0", "2.0.0"):
        _http_publish_retire(slice4_client, version=version)
    page1 = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1&limit=1",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert page1.status_code == 200
    cursor = page1.json()["next_cursor"]
    assert cursor is not None

    before = render_prometheus_metrics()
    state["sink"] = fail_sink
    page2 = slice4_client.get(
        f"/v1/ledger/retirement_history?case_id=C1&limit=1&cursor={quote(cursor, safe='')}",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert page2.status_code == 503
    after = render_prometheus_metrics()

    assert _catalog_total(
        after,
        query_kind="get_retirement_history",
        outcome="ok",
        error_kind=ERROR_KIND_NONE,
    ) == _catalog_total(
        before,
        query_kind="get_retirement_history",
        outcome="ok",
        error_kind=ERROR_KIND_NONE,
    )
    assert _rows_returned_total(after, query_kind="get_retirement_history") == _rows_returned_total(
        before, query_kind="get_retirement_history"
    )
    assert _metric_line(
        after,
        "audit_sink_failure_total",
        sink="primary",
        tenant_id=TENANT,
    ) - _metric_line(
        before,
        "audit_sink_failure_total",
        sink="primary",
        tenant_id=TENANT,
    ) == 1


def test_i_e3_13_rows_returned_uses_payload_not_db_count(slice4_client, monkeypatch):
    """DB match count N, simulated redaction drops K — metric += (N-K)."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)

    original = AuditingAuthoringCatalogReadService._catalog_payload_row_count
    redaction_drop = 1

    def _simulated_post_redaction(self, result):
        db_count = original(self, result)
        return max(0, db_count - redaction_drop)

    monkeypatch.setattr(
        AuditingAuthoringCatalogReadService,
        "_catalog_payload_row_count",
        _simulated_post_redaction,
    )

    _http_publish_retire(slice4_client, version="1.0.0")
    _http_publish_retire(slice4_client, version="2.0.0")
    before = render_prometheus_metrics()
    resp = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1&limit=2",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    n = len(items)
    assert n == 2
    ok = [e for e in audit.events if e.query_kind == "get_retirement_history" and e.outcome == "ok"]
    assert ok[-1].result_row_count == n
    after = render_prometheus_metrics()
    delta = _rows_returned_total(after, query_kind="get_retirement_history") - _rows_returned_total(
        before, query_kind="get_retirement_history"
    )
    assert delta == n - redaction_drop
    assert delta != n


def test_i_e3_14_no_schema_or_migration_files_in_e3_c_a_scope():
    for rel in _E3_C_A_TOUCHED:
        assert "migrations" not in rel.lower()
        assert not rel.endswith(".sql")
        path = _REPO_ROOT / rel
        assert path.exists(), rel
    migrations = _REPO_ROOT / "docs" / "migrations"
    if migrations.is_dir():
        for path in migrations.iterdir():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "ledger_read_catalog" not in text
            assert "catalog_metrics" not in text
