"""F.b.1: list_learner_sessions route + cursor pagination (smoke)."""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

from services.pratibimb.audit.metrics import render_prometheus_metrics
from services.pratibimb.audit.sink import InMemoryAuditSink
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.ledger.db import get_engine, init_ledger_schema
from services.pratibimb.ledger.models import SessionEvidenceProjectionRow
from services.pratibimb.ledger_read.deps import get_in_memory_consent_store, reset_auth_wiring_cache
from services.pratibimb.ledger_read.errors import InvalidNcvetCursorError
from services.pratibimb.ledger_read.ncvet_cursor import decode_ncvet_cursor, encode_ncvet_cursor
from services.pratibimb.ledger_read.ncvet_metrics import reset_ncvet_metrics_for_tests
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    _dev_auth,
    slice4_client,
)
from shared.schemas.ledger_read import ConsentScope

NCVET_LIST_READER = "ncvet-list-15"
LEARNER_A = "learner-a"
LEARNER_B = "learner-b"
NOW = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)


def _patch_audit_sink(monkeypatch, audit: InMemoryAuditSink) -> None:
    from services.pratibimb.ledger_read import deps as ledger_deps
    import services.pratibimb.ledger_read.routes as ledger_routes

    ledger_deps.get_audit_sink.cache_clear()
    monkeypatch.setattr(ledger_deps, "get_audit_sink", lambda: audit)
    monkeypatch.setattr(ledger_routes, "get_audit_sink", lambda: audit)
    reset_auth_wiring_cache()


def _ncvet_total(body: str, *, query_kind: str, outcome: str, error_kind: str) -> int:
    label_str = ",".join(
        f'{k}="{v}"'
        for k, v in sorted({
            "tenant_id": TENANT,
            "query_kind": query_kind,
            "outcome": outcome,
            "error_kind": error_kind,
        }.items())
    )
    needle = f"ledger_read_ncvet_total{{{label_str}}} "
    for line in body.splitlines():
        if line.startswith(needle):
            return int(line.rsplit(" ", 1)[-1])
    return 0


def _seed_list_scope() -> None:
    asyncio.run(
        seed_authoring_scope(
            tenant_id=TENANT,
            subject_id=NCVET_LIST_READER,
            scope=ConsentScope.NCVET_READ_LEARNER_SESSIONS,
            store=get_in_memory_consent_store(),
        )
    )


def _seed_sessions(*, learner: str, count: int, prefix: str) -> None:
    get_engine.cache_clear()
    engine = get_engine()
    init_ledger_schema(engine)
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        for i in range(count):
            at = NOW - timedelta(hours=i)
            session.add(
                SessionEvidenceProjectionRow(
                    session_id=f"{prefix}-sess-{i}",
                    tenant_id=TENANT,
                    learner_pseudo_id=learner,
                    cohort_id="cohort-a",
                    case_id="C1",
                    case_version="1.0.0",
                    finalized_at_utc=at,
                    physio_engine_version="physio@v1",
                    rubric_version="0.3.0",
                    replay_hash=f"hash-{prefix}-{i}",
                    grade_total=0.8,
                    grade_passed=True,
                    axis_normalized={},
                    evidence=[],
                    flags=[],
                    actions=[],
                    case_context_json="{}",
                    projected_at=at,
                )
            )
        session.commit()


@pytest.fixture(autouse=True)
def _f_b1_scope(slice4_client):
    reset_ncvet_metrics_for_tests()
    get_engine.cache_clear()
    _seed_list_scope()
    yield slice4_client
    get_engine.cache_clear()
    reset_ncvet_metrics_for_tests()


def test_list_learner_sessions_happy_path_and_cursor_page(slice4_client):
    _seed_sessions(learner=LEARNER_A, count=3, prefix="a")
    resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=2",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"]
    page2 = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=2"
        f"&cursor={body['next_cursor']}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert page2.status_code == 200
    assert len(page2.json()["items"]) == 1


def test_cross_learner_cursor_replay_400_not_empty_page(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    before = render_prometheus_metrics()
    _seed_sessions(learner=LEARNER_A, count=2, prefix="a")
    _seed_sessions(learner=LEARNER_B, count=1, prefix="b")
    page_a = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=1",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    cursor_a = page_a.json()["next_cursor"]
    assert cursor_a
    replay = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_B}&limit=1"
        f"&cursor={cursor_a}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert replay.status_code == 400
    assert replay.json()["detail"]["error"] == "cursor_invalid"
    invalid = [
        e
        for e in audit.events
        if e.query_kind == "list_learner_sessions" and e.error_kind == "cursor_invalid"
    ]
    assert len(invalid) == 1
    assert invalid[0].outcome == "error"
    assert invalid[0].result_row_count is None
    after = render_prometheus_metrics()
    assert _ncvet_total(
        after,
        query_kind="list_learner_sessions",
        outcome="error",
        error_kind="cursor_invalid",
    ) - _ncvet_total(
        before,
        query_kind="list_learner_sessions",
        outcome="error",
        error_kind="cursor_invalid",
    ) == 1


def test_decode_ncvet_cursor_rejects_extra_keys():
    payload = {
        "at": NOW.isoformat(),
        "learner": LEARNER_A,
        "ord": 2,
        "sid": "sess-1",
        "tenant": TENANT,
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode()
    ).decode().rstrip("=")
    with pytest.raises(InvalidNcvetCursorError):
        decode_ncvet_cursor(raw, learner_pseudo_id=LEARNER_A)


def test_forged_page_ordinal_in_cursor_does_not_change_result_rows(slice4_client):
    """Keyset uses (at, sid) only — bogus ``ord`` must not alter returned rows."""
    _seed_sessions(learner=LEARNER_A, count=3, prefix="a")
    page1 = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=1",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    good_cursor = page1.json()["next_cursor"]
    at, sid, _ord = decode_ncvet_cursor(good_cursor, learner_pseudo_id=LEARNER_A)
    forged_payload = {
        "at": at.isoformat(),
        "learner": LEARNER_A,
        "ord": 999,
        "sid": sid,
    }
    forged_cursor = base64.urlsafe_b64encode(
        json.dumps(forged_payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    good_page = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=1"
        f"&cursor={good_cursor}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    forged_page = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=1"
        f"&cursor={forged_cursor}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert good_page.status_code == 200
    assert forged_page.status_code == 200
    assert good_page.json()["items"] == forged_page.json()["items"]
