"""E3.b.1: retirement history audited read + fail-closed + scope-split matrix."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.authoring.constants import RetireReasonCode
from services.pratibimb.ledger_read.deps import reset_auth_wiring_cache
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    CATALOG_READER,
    OTHER_TENANT,
    _dev_auth,
    _http_publish_retire,
    slice4_client,
)
from shared.schemas.ledger_read import ConsentScope

RETIREMENT_READER = "retirement-reader-8"
PUBLISHED_ONLY = "published-only-9"
RETIREMENT_ONLY = "retirement-only-10"
BOTH_READER = "both-reader-11"
NO_SCOPE = "no-scope-12"


@dataclass
class _TrackingFailingSink:
    """Records emit attempts then fails — tests audit *attempt*, not persistence."""

    calls: list[AuditEvent] = field(default_factory=list)

    def emit(self, event: AuditEvent) -> None:
        self.calls.append(event)
        raise RuntimeError("simulated primary sink failure")


def _patch_audit_sink(monkeypatch, audit) -> None:
    from services.pratibimb.ledger_read import deps as ledger_deps
    import services.pratibimb.ledger_read.routes as ledger_routes

    ledger_deps.get_audit_sink.cache_clear()
    monkeypatch.setattr(ledger_deps, "get_audit_sink", lambda: audit)
    monkeypatch.setattr(ledger_routes, "get_audit_sink", lambda: audit)
    reset_auth_wiring_cache()


def _seed_scope(subject: str, scope: ConsentScope) -> None:
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store

    asyncio.run(
        seed_authoring_scope(
            tenant_id=TENANT,
            subject_id=subject,
            scope=scope,
            store=get_in_memory_consent_store(),
        )
    )


@pytest.fixture(autouse=True)
def _e3_b1_scopes(slice4_client):
    _seed_scope(RETIREMENT_READER, ConsentScope.AUTHORING_READ_RETIREMENT_HISTORY)
    _seed_scope(PUBLISHED_ONLY, ConsentScope.AUTHORING_READ_PUBLISHED)
    _seed_scope(RETIREMENT_ONLY, ConsentScope.AUTHORING_READ_RETIREMENT_HISTORY)
    _seed_scope(BOTH_READER, ConsentScope.AUTHORING_READ_PUBLISHED)
    _seed_scope(BOTH_READER, ConsentScope.AUTHORING_READ_RETIREMENT_HISTORY)
    yield slice4_client

def _assert_scope_denial_audit(event: AuditEvent, *, error_kind: str) -> None:
    assert event.outcome == "scope_denied"
    assert event.error_kind == error_kind
    assert event.result_row_count is None
    assert event.query_params_hash
    assert event.query_params_bytes > 0


def test_fail_closed_sink_emit_attempted_no_retirement_data(slice4_client, monkeypatch):
    """Pin A: emit invoked once; 5xx JSON envelope; body has no retirement rows."""
    sink = _TrackingFailingSink()
    _patch_audit_sink(monkeypatch, sink)
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert resp.status_code == 503
    body = resp.json()
    assert isinstance(body, dict)
    detail = body["detail"]
    assert detail["error"] == "audit_unavailable"
    assert detail.get("correlation_id")
    assert "retry_after_seconds" in detail
    assert not isinstance(body, list)
    assert "reason_code" not in resp.text
    assert len(sink.calls) == 1
    assert sink.calls[0].query_kind == "get_retirement_history"
    assert sink.calls[0].outcome == "ok"
    assert sink.calls[0].result_row_count is not None


def test_scope_matrix_published_only(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    pub = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1",
        headers=_dev_auth(PUBLISHED_ONLY),
    )
    assert pub.status_code == 200
    ret = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1",
        headers=_dev_auth(PUBLISHED_ONLY),
    )
    assert ret.status_code == 403
    assert ret.json()["detail"]["error"] == "scope_denied"
    denied = [e for e in audit.events if e.query_kind == "get_retirement_history"]
    assert len(denied) == 1
    _assert_scope_denial_audit(denied[0], error_kind="consent_scope")


def test_scope_matrix_retirement_only(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    pub = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1",
        headers=_dev_auth(RETIREMENT_ONLY),
    )
    assert pub.status_code == 403
    denied_pub = [
        e for e in audit.events if e.query_kind == "get_published_case_versions"
    ]
    assert len(denied_pub) == 1
    _assert_scope_denial_audit(denied_pub[0], error_kind="consent_scope")
    ret = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1",
        headers=_dev_auth(RETIREMENT_ONLY),
    )
    assert ret.status_code == 200
    assert ret.json()["items"][0]["reason_code"] == RetireReasonCode.POLICY_CHANGE.value


def test_scope_matrix_both_grants(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    pub = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1",
        headers=_dev_auth(BOTH_READER),
    )
    ret = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1",
        headers=_dev_auth(BOTH_READER),
    )
    assert pub.status_code == 200
    assert ret.status_code == 200
    ok = [e for e in audit.events if e.outcome == "ok"]
    kinds = {e.query_kind for e in ok}
    assert "get_published_case_versions" in kinds
    assert "get_retirement_history" in kinds


def test_scope_matrix_neither_grant(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    pub = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1",
        headers=_dev_auth(NO_SCOPE),
    )
    ret = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1",
        headers=_dev_auth(NO_SCOPE),
    )
    assert pub.status_code == 403
    assert ret.status_code == 403
    denied = [e for e in audit.events if e.outcome == "scope_denied"]
    assert len(denied) == 2
    for event in denied:
        _assert_scope_denial_audit(event, error_kind="consent_scope")


def test_tenant_mismatch_retirement_audit_shape(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        f"/v1/ledger/retirement_history?case_id=C1&tenant_id={OTHER_TENANT}",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "tenant_mismatch"
    denied = [e for e in audit.events if e.query_kind == "get_retirement_history"]
    assert len(denied) == 1
    _assert_scope_denial_audit(denied[0], error_kind="tenant_mismatch")


def test_zero_row_retirement_still_audits(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=no-such-case",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    ok = [e for e in audit.events if e.query_kind == "get_retirement_history"]
    assert ok[-1].outcome == "ok"
    assert ok[-1].result_row_count == 0


def test_read_your_writes_retire_then_list(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["items"]
    assert len(rows) == 1
    assert rows[0]["version"] == "1.0.0"
    assert rows[0]["reason_code"] == RetireReasonCode.POLICY_CHANGE.value
    assert rows[0]["retired_by"]


def test_fail_closed_propagates_audit_write_error_not_swallowed():
    """Unit: successful read + failing sink → AuditWriteError, no result escape."""

    from services.pratibimb.auth.context import AuthContext
    from datetime import datetime, timedelta, timezone

    from services.pratibimb.ledger_read.catalog_audit import AuditingAuthoringCatalogReadService
    from services.pratibimb.ledger_read.catalog_service import (
        AuthoringCatalogReadService,
        RetirementHistoryFilter,
    )

    class _StubInner(AuthoringCatalogReadService):
        def get_retirement_history(self, filt, *, auth):
            from services.pratibimb.ledger_read.catalog_service import CatalogPageResult

            return CatalogPageResult(
                items=[{"id": "r1", "reason_code": "policy_change"}],  # type: ignore[list-item]
            )

    sink = _TrackingFailingSink()
    svc = AuditingAuthoringCatalogReadService(_StubInner(None), sink)  # type: ignore[arg-type]
    auth = AuthContext(
        subject_pseudo_id=RETIREMENT_READER,
        tenant_id=TENANT,
        jti="j",
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        source="dev",
    )

    async def _call():
        return await svc.get_retirement_history(
            RetirementHistoryFilter(tenant_id=TENANT, case_id="C1"),
            scope=ConsentScope.AUTHORING_READ_RETIREMENT_HISTORY,
            auth=auth,
        )

    with pytest.raises(AuditWriteError):
        asyncio.run(_call())
    assert len(sink.calls) == 1
