"""E3.b.2: cursor pagination + per-page audit (I-E3-7)."""
from __future__ import annotations

from urllib.parse import quote

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
    "services.pratibimb.tests.test_authoring_e3_b1",
]

from services.pratibimb.audit.sink import InMemoryAuditSink
from services.pratibimb.authoring.constants import RetireReasonCode
from services.pratibimb.ledger_read.catalog_cursor import encode_catalog_cursor
from services.pratibimb.tests.test_authoring_e3_b1 import (
    RETIREMENT_READER,
    _patch_audit_sink,
)
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    _dev_auth,
    _http_publish_retire,
    slice4_client,
)
from shared.schemas.ledger_read import ConsentScope


def _items(resp) -> list:
    body = resp.json()
    assert isinstance(body, dict)
    assert "items" in body
    return body["items"]


def _http_publish_only(client, *, version: str = "1.0.0") -> None:
    """Publish without retire — for multi-version pagination fixtures."""
    from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
        AUTHOR,
        PUBLISHER,
        REVIEWER,
        _full_blueprint,
        _pass_trace,
        _miss_trace,
    )
    from services.pratibimb.authoring.constants import FixtureKind

    bp = _full_blueprint(version=version)
    created = client.post(
        "/v1/authoring/drafts",
        headers=_dev_auth(AUTHOR),
        json={"blueprint_json": bp, "blueprint_version": version},
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]
    for kind, passed, trace in (
        (FixtureKind.PERFECT_PATH, True, _pass_trace(case_version=version)),
        (FixtureKind.CRITICAL_MISS, False, _miss_trace(case_version=version)),
    ):
        client.put(
            f"/v1/authoring/drafts/{draft_id}/fixtures",
            headers=_dev_auth(AUTHOR),
            json={
                "fixture_kind": kind.value,
                "trace_json": trace,
                "expected_grade_json": {"passed": passed},
            },
        ).raise_for_status()
    client.post(
        f"/v1/authoring/drafts/{draft_id}/submit",
        headers=_dev_auth(AUTHOR),
        json={},
    ).raise_for_status()
    client.post(
        f"/v1/authoring/drafts/{draft_id}/approve",
        headers=_dev_auth(REVIEWER),
        json={},
    ).raise_for_status()
    client.post(
        f"/v1/authoring/drafts/{draft_id}/publish",
        headers=_dev_auth(PUBLISHER),
    ).raise_for_status()


def test_limit_101_returns_400(slice4_client):
    resp = slice4_client.get(
        "/v1/ledger/retirement_history?limit=101",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert resp.status_code == 422


def test_n_pages_n_audit_rows_distinct_hashes(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    for version in ("3.0.0", "2.0.0", "1.0.0"):
        _http_publish_retire(slice4_client, version=version)
    cursor = None
    hashes: list[str] = []
    for _ in range(3):
        url = "/v1/ledger/retirement_history?case_id=C1&limit=1"
        if cursor:
            url += f"&cursor={quote(cursor, safe='')}"
        resp = slice4_client.get(url, headers=_dev_auth(RETIREMENT_READER))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["items"]) == 1
        cursor = body.get("next_cursor")
        page_events = [
            e
            for e in audit.events
            if e.query_kind == "get_retirement_history" and e.outcome == "ok"
        ]
        hashes.append(page_events[-1].query_params_hash)
    assert len(set(hashes)) == 3
    assert hashes[0] != hashes[1] != hashes[2]


def test_tampered_cursor_400_and_cursor_invalid_audit(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _http_publish_retire(slice4_client, version="1.0.0")
    _http_publish_retire(slice4_client, version="2.0.0")
    first = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1&limit=1",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor is not None
    tampered = cursor[:-2] + ("AA" if cursor[-2:] != "AA" else "BB")
    resp = slice4_client.get(
        f"/v1/ledger/retirement_history?case_id=C1&cursor={quote(tampered, safe='')}",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "cursor_invalid"
    invalid = [e for e in audit.events if e.error_kind == "cursor_invalid"]
    assert len(invalid) == 1
    assert invalid[0].query_params_hash


def test_published_list_paginated_shape(slice4_client):
    _http_publish_retire(slice4_client)
    resp = slice4_client.get(
        "/v1/ledger/published_case_versions?case_id=C1",
        headers=_dev_auth("published-only-9"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "next_cursor" in body
    assert len(body["items"]) >= 1


def test_mid_pagination_audit_failure_503_envelope(slice4_client, monkeypatch):
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
    for version in ("1.0.0", "2.0.0"):
        _http_publish_retire(slice4_client, version=version)
    page1 = slice4_client.get(
        "/v1/ledger/retirement_history?case_id=C1&limit=1",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert page1.status_code == 200
    cursor = page1.json()["next_cursor"]
    assert cursor is not None
    state["sink"] = fail_sink
    page2 = slice4_client.get(
        f"/v1/ledger/retirement_history?case_id=C1&limit=1&cursor={quote(cursor, safe='')}",
        headers=_dev_auth(RETIREMENT_READER),
    )
    assert page2.status_code == 503
    detail = page2.json()["detail"]
    assert detail["error"] == "audit_unavailable"
    assert detail.get("correlation_id") is not None
    assert "retry_after_seconds" in detail
    assert "reason_code" not in page2.text
    assert len(fail_sink.calls) == 1
