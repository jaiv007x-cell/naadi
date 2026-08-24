"""E3.a: audited published catalog reads + legacy re-home."""
from __future__ import annotations

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

from services.pratibimb.audit.sink import InMemoryAuditSink
from services.pratibimb.ledger_read.catalog_audit import AuditingAuthoringCatalogReadService
from services.pratibimb.ledger_read.deps import reset_auth_wiring_cache
from services.pratibimb.ledger_read.query_kinds import E3_AUTHORING_CATALOG_KINDS, KNOWN_QUERY_KINDS
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    CATALOG_READER,
    OTHER_TENANT,
    REVIEWER,
    _dev_auth,
    _http_publish_retire,
    slice4_client,
)


def _patch_audit_sink(monkeypatch, audit: InMemoryAuditSink) -> None:
    from services.pratibimb.ledger_read import deps as ledger_deps
    import services.pratibimb.ledger_read.routes as ledger_routes

    ledger_deps.get_audit_sink.cache_clear()
    monkeypatch.setattr(ledger_deps, "get_audit_sink", lambda: audit)
    monkeypatch.setattr(ledger_routes, "get_audit_sink", lambda: audit)
    reset_auth_wiring_cache()


def test_i_e3_1_catalog_kinds_closed_enum():
    assert E3_AUTHORING_CATALOG_KINDS == frozenset({
        "get_published_case_versions",
        "get_retirement_history",
    })
    assert E3_AUTHORING_CATALOG_KINDS <= KNOWN_QUERY_KINDS

    class _BadCatalogAudit(AuditingAuthoringCatalogReadService):
        _REGISTERED_KINDS = frozenset({"bogus_kind"})

    from services.pratibimb.ledger_read.catalog_service import AuthoringCatalogReadService

    with pytest.raises(RuntimeError, match="E3_AUTHORING_CATALOG_KINDS"):
        _BadCatalogAudit(
            AuthoringCatalogReadService(None),  # type: ignore[arg-type]
            InMemoryAuditSink(),
        )


def test_publish_then_audited_list_sees_row(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1&include_retired=true",
        headers=_dev_auth(CATALOG_READER),
    )
    assert resp.status_code == 200, resp.text
    versions = {row["version"] for row in resp.json()["items"]}
    assert "1.0.0" in versions
    catalog_events = [
        e for e in audit.events if e.query_kind == "get_published_case_versions"
    ]
    assert catalog_events
    last = catalog_events[-1]
    assert last.outcome == "ok"
    assert last.result_row_count == len(resp.json()["items"])
    assert last.actor_subject_id == CATALOG_READER
    assert last.caller_kind == "authoring"
    assert last.subject_pseudo_id == "*"


def test_scope_deny_does_not_query_and_audits(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1",
        headers=_dev_auth("no-scope-subject"),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "scope_denied"
    denied = [e for e in audit.events if e.outcome == "scope_denied"]
    assert len(denied) == 1
    assert denied[0].error_kind == "consent_scope"
    assert denied[0].actor_subject_id == "no-scope-subject"


def test_tenant_mismatch_denies_with_audit(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        f"/v1/ledger/published_case_versions?case_id=C1&tenant_id={OTHER_TENANT}",
        headers=_dev_auth(CATALOG_READER),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "tenant_mismatch"
    denied = [e for e in audit.events if e.error_kind == "tenant_mismatch"]
    assert len(denied) == 1
    assert denied[0].outcome == "scope_denied"


def test_zero_row_list_still_audits(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=missing-case",
        headers=_dev_auth(CATALOG_READER),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    ok = [e for e in audit.events if e.query_kind == "get_published_case_versions"]
    assert ok[-1].outcome == "ok"
    assert ok[-1].result_row_count == 0


def test_legacy_authoring_published_redirects_to_ledger(slice4_client):
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/authoring/published/C1/1.0.0",
        headers=_dev_auth(CATALOG_READER),
        follow_redirects=False,
    )
    assert resp.status_code == 308
    assert resp.headers["location"] == "/v1/ledger/published_case_versions/C1/1.0.0"
    followed = slice4_client.get(
        "/v1/authoring/published/C1/1.0.0",
        headers=_dev_auth(CATALOG_READER),
        follow_redirects=True,
    )
    assert followed.status_code == 200
    assert followed.json()["version"] == "1.0.0"


def test_legacy_list_published_redirects(slice4_client):
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/authoring/published?case_id=C1&include_retired=true",
        headers=_dev_auth(CATALOG_READER),
        follow_redirects=False,
    )
    assert resp.status_code == 308
    assert resp.headers["location"] == (
        "/v1/ledger/published_case_versions?case_id=C1&include_retired=true"
    )


def test_approver_without_read_published_denied(slice4_client, monkeypatch):
    """authoring:approve does not imply read_published."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1",
        headers=_dev_auth(REVIEWER),
    )
    assert resp.status_code == 403
