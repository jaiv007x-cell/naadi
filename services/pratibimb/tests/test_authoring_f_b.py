"""F.b.2: ``list_learner_sessions`` — 12-case acceptance matrix."""
from __future__ import annotations

import asyncio
import ast
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import pytest
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import (
    AUDIT_SINK_FAILURE_TOTAL,
    render_prometheus_metrics,
)
from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.ledger.db import get_engine, init_ledger_schema
from services.pratibimb.ledger.models import Base, SessionEvidenceProjectionRow
from services.pratibimb.ledger_read.deps import get_in_memory_consent_store, reset_auth_wiring_cache
from services.pratibimb.ledger_read.ncvet_audit import AuditingNcvetReadService
from services.pratibimb.ledger_read.ncvet_metrics import reset_ncvet_metrics_for_tests
from services.pratibimb.ledger_read.ncvet_service import NcvetEvidenceReadService
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    OTHER_TENANT,
    _dev_auth,
    slice4_client,
)
from shared.schemas.ledger_read import ConsentScope

NCVET_LIST_READER = "ncvet-list-15"
SESSION_ONLY = "ncvet-session-only-16"
LIST_ONLY = "ncvet-list-only-17"
BOTH_READER = "ncvet-both-18"
NO_SCOPE = "ncvet-no-scope-19"
LEARNER_A = "learner-a"
LEARNER_B = "learner-b"
EMPTY_LEARNER = "learner-empty"
NOW = datetime(2026, 8, 22, 15, 0, tzinfo=timezone.utc)
_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class _TrackingFailingSink:
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


def _metric_line(body: str, name: str, **labels: str) -> int:
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    needle = f"{name}{{{label_str}}} "
    for line in body.splitlines():
        if line.startswith(needle):
            return int(line.rsplit(" ", 1)[-1])
    return 0


def _ncvet_total(body: str, *, query_kind: str, outcome: str, error_kind: str) -> int:
    return _metric_line(
        body,
        "ledger_read_ncvet_total",
        tenant_id=TENANT,
        query_kind=query_kind,
        outcome=outcome,
        error_kind=error_kind,
    )


def _seed_scope(subject: str, scope: ConsentScope) -> None:
    asyncio.run(
        seed_authoring_scope(
            tenant_id=TENANT,
            subject_id=subject,
            scope=scope,
            store=get_in_memory_consent_store(),
        )
    )


def _seed_sessions(*, learner: str, count: int, prefix: str) -> None:
    get_engine.cache_clear()
    engine = get_engine()
    init_ledger_schema(engine)
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
def _f_b_scopes(slice4_client):
    AUDIT_SINK_FAILURE_TOTAL._values.clear()  # type: ignore[attr-defined]
    reset_ncvet_metrics_for_tests()
    get_engine.cache_clear()
    _seed_scope(NCVET_LIST_READER, ConsentScope.NCVET_READ_LEARNER_SESSIONS)
    _seed_scope(SESSION_ONLY, ConsentScope.NCVET_READ_SESSION_EVIDENCE)
    _seed_scope(LIST_ONLY, ConsentScope.NCVET_READ_LEARNER_SESSIONS)
    _seed_scope(BOTH_READER, ConsentScope.NCVET_READ_SESSION_EVIDENCE)
    _seed_scope(BOTH_READER, ConsentScope.NCVET_READ_LEARNER_SESSIONS)
    yield slice4_client
    get_engine.cache_clear()
    reset_ncvet_metrics_for_tests()


def test_happy_path_list_audited(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_sessions(learner=LEARNER_A, count=2, prefix="a")
    resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=2",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2
    events = [e for e in audit.events if e.query_kind == "list_learner_sessions"]
    assert events[-1].outcome == "ok"
    assert events[-1].result_row_count == 2
    assert events[-1].caller_kind == "ncvet_audit"


def test_two_pages_distinct_hashes_and_page_ordinal(slice4_client, monkeypatch):
    """I-F-7: each page hashes cursor + page_ordinal → distinct query_params_hash."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_sessions(learner=LEARNER_A, count=3, prefix="a")
    cursor = None
    hashes: list[str] = []
    for _ in range(2):
        url = f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=2"
        if cursor:
            url += f"&cursor={quote(cursor, safe='')}"
        resp = slice4_client.get(url, headers=_dev_auth(NCVET_LIST_READER))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        cursor = body.get("next_cursor")
        page_events = [
            e
            for e in audit.events
            if e.query_kind == "list_learner_sessions" and e.outcome == "ok"
        ]
        hashes.append(page_events[-1].query_params_hash)
    assert len(set(hashes)) == 2
    assert hashes[0] != hashes[1]


def test_limit_101_returns_422_without_audit_or_metrics(slice4_client, monkeypatch):
    """Framework validation rejects limit>100 before gateway — silent to audit surface."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    before = render_prometheus_metrics()
    resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=101",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert resp.status_code == 422
    assert audit.events == []
    after = render_prometheus_metrics()
    assert _ncvet_total(
        after,
        query_kind="list_learner_sessions",
        outcome="ok",
        error_kind="",
    ) == _ncvet_total(
        before,
        query_kind="list_learner_sessions",
        outcome="ok",
        error_kind="",
    )


def test_scope_deny_does_not_query_and_audits(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_sessions(learner=LEARNER_A, count=1, prefix="a")
    inner_calls: list[str] = []
    original = NcvetEvidenceReadService.list_learner_sessions

    def _spy(self, filt, *, auth):
        inner_calls.append(filt.learner_pseudo_id)
        return original(self, filt, auth=auth)

    monkeypatch.setattr(NcvetEvidenceReadService, "list_learner_sessions", _spy)
    resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}",
        headers=_dev_auth(NO_SCOPE),
    )
    assert resp.status_code == 403
    assert inner_calls == []
    denied = [e for e in audit.events if e.outcome == "scope_denied"]
    assert len(denied) == 1
    assert denied[0].error_kind == "consent_scope"


def test_scope_split_matrix(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_sessions(learner=LEARNER_A, count=1, prefix="a")
    session_id = "a-sess-0"

    list_ok = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}",
        headers=_dev_auth(LIST_ONLY),
    )
    assert list_ok.status_code == 200

    session_ok = slice4_client.get(
        f"/v1/ledger/session_evidence/{session_id}",
        headers=_dev_auth(SESSION_ONLY),
    )
    assert session_ok.status_code == 200

    list_denied = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}",
        headers=_dev_auth(SESSION_ONLY),
    )
    assert list_denied.status_code == 403

    session_denied = slice4_client.get(
        f"/v1/ledger/session_evidence/{session_id}",
        headers=_dev_auth(LIST_ONLY),
    )
    assert session_denied.status_code == 403

    both_list = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}",
        headers=_dev_auth(BOTH_READER),
    )
    both_session = slice4_client.get(
        f"/v1/ledger/session_evidence/{session_id}",
        headers=_dev_auth(BOTH_READER),
    )
    assert both_list.status_code == 200
    assert both_session.status_code == 200

    neither_list = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}",
        headers=_dev_auth(NO_SCOPE),
    )
    neither_session = slice4_client.get(
        f"/v1/ledger/session_evidence/{session_id}",
        headers=_dev_auth(NO_SCOPE),
    )
    assert neither_list.status_code == 403
    assert neither_session.status_code == 403
    denied = [e for e in audit.events if e.outcome == "scope_denied"]
    assert len(denied) >= 3


def test_tenant_mismatch_list_audited(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_sessions(learner=LEARNER_A, count=1, prefix="a")
    resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}"
        f"&tenant_id={OTHER_TENANT}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert resp.status_code == 403
    denied = [e for e in audit.events if e.error_kind == "tenant_mismatch"]
    assert len(denied) == 1


def test_fail_closed_sink_failure_metric_and_no_items(slice4_client, monkeypatch):
    sink = _TrackingFailingSink()
    _patch_audit_sink(monkeypatch, sink)
    _seed_sessions(learner=LEARNER_A, count=1, prefix="a")
    before = render_prometheus_metrics()
    resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "audit_unavailable"
    assert "items" not in resp.text
    assert "next_cursor" not in resp.text
    assert len(sink.calls) == 1
    after = render_prometheus_metrics()
    assert _metric_line(
        after, "audit_sink_failure_total", sink="primary", tenant_id=TENANT
    ) - _metric_line(
        before, "audit_sink_failure_total", sink="primary", tenant_id=TENANT
    ) == 1


def test_fail_closed_mid_pagination_cursor_preserved(slice4_client, monkeypatch):
    ok_sink = InMemoryAuditSink()
    fail_sink = _TrackingFailingSink()
    state = {"sink": ok_sink}

    def _rotating_sink():
        return state["sink"]

    from services.pratibimb.ledger_read import deps as ledger_deps
    import services.pratibimb.ledger_read.routes as ledger_routes

    ledger_deps.get_audit_sink.cache_clear()
    monkeypatch.setattr(ledger_deps, "get_audit_sink", _rotating_sink)
    monkeypatch.setattr(ledger_routes, "get_audit_sink", _rotating_sink)
    reset_auth_wiring_cache()

    _seed_sessions(learner=LEARNER_A, count=3, prefix="a")
    page1 = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=2",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert page1.status_code == 200
    cursor = page1.json()["next_cursor"]
    assert cursor

    state["sink"] = fail_sink
    page2_fail = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=2"
        f"&cursor={quote(cursor, safe='')}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert page2_fail.status_code == 503
    assert "items" not in page2_fail.text

    state["sink"] = ok_sink
    page2_retry = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_A}&limit=2"
        f"&cursor={quote(cursor, safe='')}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert page2_retry.status_code == 200
    assert len(page2_retry.json()["items"]) == 1


def test_cross_learner_cursor_replay_audited_not_empty_page(slice4_client, monkeypatch):
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
    replay = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={LEARNER_B}&limit=1"
        f"&cursor={cursor_a}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert replay.status_code == 400
    assert replay.json()["detail"]["error"] == "cursor_invalid"
    invalid = [e for e in audit.events if e.error_kind == "cursor_invalid"]
    assert len(invalid) == 1
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


def test_list_projection_read_hits_projection_table_not_ledger():
    engine = __import__("sqlalchemy").create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    session.add(
        SessionEvidenceProjectionRow(
            session_id="list-sess-1",
            tenant_id=TENANT,
            learner_pseudo_id=LEARNER_A,
            cohort_id="c1",
            case_id="C1",
            case_version="1.0.0",
            finalized_at_utc=NOW,
            physio_engine_version="v1",
            rubric_version="r1",
            replay_hash="h1",
            grade_total=1.0,
            grade_passed=True,
            axis_normalized={},
            evidence=[],
            flags=[],
            actions=[],
            case_context_json="{}",
            projected_at=NOW,
        )
    )
    session.commit()

    from services.pratibimb.auth.context import AuthContext

    auth = AuthContext(
        subject_pseudo_id=NCVET_LIST_READER,
        tenant_id=TENANT,
        jti="j",
        issued_at=NOW,
        expires_at=NOW,
        source="dev",
    )
    touched: list[str] = []
    original_scalars = session.scalars

    def _spy(statement, *args, **kwargs):
        sql = str(statement).lower()
        if "session_evidence_projection" in sql:
            touched.append("projection")
        if "session_ledger" in sql:
            touched.append("ledger")
        return original_scalars(statement, *args, **kwargs)

    svc = NcvetEvidenceReadService(session)
    from services.pratibimb.ledger_read.ncvet_service import LearnerSessionsFilter

    with patch.object(session, "scalars", side_effect=_spy):
        page = svc.list_learner_sessions(
            LearnerSessionsFilter(tenant_id=TENANT, learner_pseudo_id=LEARNER_A),
            auth=auth,
        )
    assert len(page.items) == 1
    assert "projection" in touched
    assert "ledger" not in touched

    import services.pratibimb.ledger_read.ncvet_service as ncvet_mod

    tree = ast.parse(Path(ncvet_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "ledger.models" in node.module:
                imported = {alias.name for alias in node.names}
                assert "SessionLedgerRow" not in imported


def test_f_ncvet_list_kind_closed_enum():
    class _Bad(AuditingNcvetReadService):
        _REGISTERED_KINDS = frozenset({"bogus_list_kind"})

    with pytest.raises(RuntimeError, match="F_NCVET_QUERY_KINDS"):
        _Bad(NcvetEvidenceReadService(None), InMemoryAuditSink())  # type: ignore[arg-type]
    assert "list_learner_sessions" in F_NCVET_QUERY_KINDS


def test_zero_row_list_still_audits(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.get(
        f"/v1/ledger/learner_sessions?learner_pseudo_id={EMPTY_LEARNER}",
        headers=_dev_auth(NCVET_LIST_READER),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    ok = [e for e in audit.events if e.query_kind == "list_learner_sessions"]
    assert ok[-1].outcome == "ok"
    assert ok[-1].result_row_count == 0
