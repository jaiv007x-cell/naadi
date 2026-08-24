"""F.c.b: NCVET observability — I-E3-11 deny-path metric/audit parity."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
    "services.pratibimb.tests.test_authoring_f_b",
]

from services.pratibimb.audit.metrics import render_prometheus_metrics
from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink
from services.pratibimb.ledger_read.ncvet_metrics import (
    ERROR_KIND_NONE,
    NcvetMetricsError,
    record_ncvet_audit_metrics,
    reset_ncvet_metrics_for_tests,
)
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_f_b import (
    LEARNER_A,
    NCVET_LIST_READER,
    NO_SCOPE,
    OTHER_TENANT,
    _ncvet_total,
    _patch_audit_sink,
    _seed_sessions,
    slice4_client,
)
from services.pratibimb.tests.test_authoring_phase_d_slice4 import _dev_auth


def _metric_line(body: str, name: str, **labels: str) -> int:
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    needle = f"{name}{{{label_str}}} "
    for line in body.splitlines():
        if line.startswith(needle):
            return int(line.rsplit(" ", 1)[-1])
    return 0


@pytest.fixture(autouse=True)
def _f_c_b_metrics_reset():
    reset_ncvet_metrics_for_tests()
    yield
    reset_ncvet_metrics_for_tests()


def test_i_e3_11_ncvet_error_kinds_and_outcome_split_on_metrics(slice4_client, monkeypatch):
    """Three deny paths: scope_denied + tenant_mismatch + error cursor_invalid (I-E3-11)."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_sessions(learner=LEARNER_A, count=2, prefix="a")

    scope_resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}",
        headers=_dev_auth(NO_SCOPE),
    )
    assert scope_resp.status_code == 403

    tenant_resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}"
        f"&tenant_id={OTHER_TENANT}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert tenant_resp.status_code == 403

    first = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=1",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor is not None
    tampered = cursor[:-2] + ("AA" if cursor[-2:] != "AA" else "BB")
    cursor_resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=1"
        f"&cursor={quote(tampered, safe='')}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert cursor_resp.status_code == 400

    body = render_prometheus_metrics()
    assert _ncvet_total(
        body,
        query_kind="list_learner_sessions",
        outcome="scope_denied",
        error_kind="consent_scope",
    ) == 1
    assert _ncvet_total(
        body,
        query_kind="list_learner_sessions",
        outcome="scope_denied",
        error_kind="tenant_mismatch",
    ) == 1
    assert _ncvet_total(
        body,
        query_kind="list_learner_sessions",
        outcome="error",
        error_kind="cursor_invalid",
    ) == 1
    assert _ncvet_total(
        body,
        query_kind="list_learner_sessions",
        outcome="scope_denied",
        error_kind="cursor_invalid",
    ) == 0
    invalid = [e for e in audit.events if e.error_kind == "cursor_invalid"]
    assert len(invalid) == 1
    assert invalid[0].result_row_count is None


def test_i_e3_11_ncvet_unknown_error_kind_raises():
    event = AuditEvent(
        query_id="q-ncvet-metrics",
        at_utc=datetime(2026, 8, 22, tzinfo=timezone.utc),
        tenant_id=TENANT,
        subject_pseudo_id="*",
        caller_kind="ncvet_audit",
        scope="ncvet:read_learner_sessions",
        query_kind="list_learner_sessions",
        query_params_hash="abc",
        query_params_bytes=3,
        outcome="error",
        duration_ms=0,
        error_kind="rate_limited",
    )
    with pytest.raises(NcvetMetricsError, match="error_kind"):
        record_ncvet_audit_metrics(event)
