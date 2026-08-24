"""Summative-restricted mode middleware and guard tests."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.pratibimb.app.main import app, reset_summative_guard_for_tests
from services.pratibimb.ledger_read.fallback_sink import JsonlFallbackSink
from services.pratibimb.ledger_read.summative_guard import (
    CRITICAL_FAILURES_PER_MINUTE,
    RESTRICTION_REASONS,
    SummativeRestrictionGuard,
    SummativeStartupError,
    assert_valid_restriction_reason,
    record_primary_sink_failure,
    validate_summative_startup,
)

UTC = timezone.utc


@pytest.fixture
def fallback(tmp_path) -> JsonlFallbackSink:
    return JsonlFallbackSink(tmp_path)


@pytest.fixture
def guard(fallback) -> SummativeRestrictionGuard:
    return SummativeRestrictionGuard(
        fallback,
        critical_failures_per_minute=CRITICAL_FAILURES_PER_MINUTE,
        fallback_unhealthy_seconds=3600,
    )


@pytest.fixture
def client(guard):
    reset_summative_guard_for_tests(guard)
    yield TestClient(app)
    reset_summative_guard_for_tests(None)


def test_formative_caller_passes_when_fallback_unhealthy(client, guard, fallback):
    fallback._last_failure_at = datetime.now(UTC)
    assert guard.is_restricted() is True

    response = client.get(
        "/v1/ledger/sessions",
        params={"cohort_id": "c1"},
        headers={"x-dev-subject": "p1", "x-dev-tenant": "t1"},
    )
    # No summative caller header → middleware does not block (auth may 401/403 elsewhere).
    assert response.status_code != 503


def test_summative_caller_gets_503_when_fallback_unhealthy(client, guard, fallback):
    fallback._last_failure_at = datetime.now(UTC)

    response = client.get(
        "/v1/ledger/sessions",
        params={"cohort_id": "c1"},
        headers={
            "x-ledger-caller-kind": "regulator",
            "x-dev-subject": "reg-1",
            "x-dev-tenant": "tenant-a",
        },
    )
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "60"
    body = response.json()
    assert body["detail"]["error"] == "summative_restricted"
    assert body["detail"]["reason"] == "fallback_sink_unhealthy"


def test_summative_caller_passes_when_healthy(client, guard, fallback):
    assert fallback.is_healthy() is True

    response = client.get(
        "/v1/ledger/sessions",
        params={"cohort_id": "c1"},
        headers={
            "x-ledger-caller-kind": "ncvet_audit",
            "x-dev-subject": "aud-1",
            "x-dev-tenant": "tenant-a",
        },
    )
    assert response.status_code != 503


def test_summative_caller_gets_503_on_primary_failure_rate(client, guard, fallback):
    tenant = "tenant-rate-test"
    for _ in range(CRITICAL_FAILURES_PER_MINUTE):
        record_primary_sink_failure(tenant)

    response = client.get(
        "/health",
        headers={
            "x-ledger-caller-kind": "insurer_aggregate",
            "x-dev-tenant": tenant,
        },
    )
    # /health is not under /v1/ledger — middleware must not block.
    assert response.status_code == 200


def test_summative_caller_503_on_ledger_path_for_failure_rate(client, guard, fallback):
    tenant = "tenant-rate-ledger"
    for _ in range(CRITICAL_FAILURES_PER_MINUTE):
        record_primary_sink_failure(tenant)

    response = client.get(
        "/v1/ledger/sessions",
        params={"cohort_id": "c1"},
        headers={
            "x-ledger-caller-kind": "insurer_aggregate",
            "x-dev-tenant": tenant,
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "primary_sink_failure_rate_exceeded"


def test_restriction_reason_is_closed_vocabulary():
    for reason in RESTRICTION_REASONS:
        assert_valid_restriction_reason(reason)


def test_invalid_restriction_reason_rejected():
    with pytest.raises(ValueError, match="not in"):
        assert_valid_restriction_reason("disk_full")


def test_production_refuses_boot_with_guard_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("SUMMATIVE_RESTRICTION_ENABLED", "false")
    with pytest.raises(SummativeStartupError, match="forbidden"):
        validate_summative_startup()


def test_guard_disabled_via_env(client, guard, fallback, monkeypatch):
    fallback._last_failure_at = datetime.now(UTC)
    monkeypatch.setenv("SUMMATIVE_RESTRICTION_ENABLED", "false")

    response = client.get(
        "/v1/ledger/sessions",
        headers={"x-ledger-caller-kind": "regulator", "x-dev-tenant": "t1"},
    )
    assert response.status_code != 503
