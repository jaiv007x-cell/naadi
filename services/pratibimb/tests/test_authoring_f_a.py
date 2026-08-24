"""F.a: NCVET ``get_session_evidence`` — 7-case acceptance matrix."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

from services.pratibimb.audit.caller_kinds import (
    KNOWN_AUDIT_CALLER_KINDS,
    assert_known_caller_kind,
)
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import (
    AUDIT_SINK_FAILURE_TOTAL,
    render_prometheus_metrics,
)
from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink, validate_audit_event
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.ledger.db import get_engine, init_ledger_schema
from services.pratibimb.ledger.models import Base, SessionEvidenceProjectionRow
from services.pratibimb.ledger_read.deps import reset_auth_wiring_cache
from services.pratibimb.ledger_read.ncvet_audit import AuditingNcvetReadService
from services.pratibimb.ledger_read.ncvet_redaction import EXCLUDED_FIELD_NAMES
from services.pratibimb.ledger_read.ncvet_service import NcvetEvidenceReadService
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS, KNOWN_QUERY_KINDS
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    OTHER_TENANT,
    _dev_auth,
    _full_blueprint,
    slice4_client,
)
from shared.schemas.ledger_read import ConsentScope

NCVET_READER = "ncvet-reader-13"
NO_NCVET_SCOPE = "no-ncvet-scope-14"
SESSION_ID = "sess-ncvet-f-a-1"
_REPO_ROOT = Path(__file__).resolve().parents[3]

UTC = timezone.utc
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


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


def _seed_ncvet_scope(subject: str) -> None:
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store

    asyncio.run(
        seed_authoring_scope(
            tenant_id=TENANT,
            subject_id=subject,
            scope=ConsentScope.NCVET_READ_SESSION_EVIDENCE,
            store=get_in_memory_consent_store(),
        )
    )


def _redacted_case_context() -> str:
    from services.pratibimb.ledger_read.ncvet_redaction import redact_case_context_json

    envelope = {"blueprint": _full_blueprint()}
    return redact_case_context_json(json.dumps(envelope))


def _seed_projection(
    *,
    session_id: str = SESSION_ID,
    tenant_id: str = TENANT,
) -> None:
    get_engine.cache_clear()
    engine = get_engine()
    init_ledger_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        session.add(
            SessionEvidenceProjectionRow(
                session_id=session_id,
                tenant_id=tenant_id,
                learner_pseudo_id="learner-pseudo-1",
                cohort_id="cohort-a",
                case_id="C1",
                case_version="1.0.0",
                finalized_at_utc=NOW,
                physio_engine_version="physio@v1",
                rubric_version="0.3.0",
                replay_hash="replay-hash-f-a",
                blueprint_content_hash="a" * 64,
                blueprint_source="published",
                grade_total=0.85,
                grade_passed=True,
                axis_normalized={"action": 0.9},
                evidence=[{"kind": "hit", "id": "aspirin"}],
                flags=[],
                actions=[{"canonical_key": "give_aspirin"}],
                case_context_json=_redacted_case_context(),
                projected_at=NOW,
            )
        )
        session.commit()


def _grep_excluded_field_names(payload: str) -> list[str]:
    """
    Grep serialized JSON text for excluded object keys at any nesting depth.

    Uses the wire-format response (``resp.text``), not a top-level dict walk, so
    keys inside nested objects and arrays-of-objects are visible. Defeated only if
    projection stores encoded blobs (see I-F-5 co-requirement in ncvet_redaction).
    """
    hits: list[str] = []
    for name in EXCLUDED_FIELD_NAMES:
        if re.search(rf'"{re.escape(name)}"\s*:', payload):
            hits.append(name)
    return hits


def _metric_line(body: str, name: str, **labels: str) -> int:
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    needle = f"{name}{{{label_str}}} "
    for line in body.splitlines():
        if line.startswith(needle):
            return int(line.rsplit(" ", 1)[-1])
    return 0


def _reset_sink_failure_metric() -> None:
    AUDIT_SINK_FAILURE_TOTAL._values.clear()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _f_a_metrics_reset():
    _reset_sink_failure_metric()
    yield
    _reset_sink_failure_metric()


@pytest.fixture(autouse=True)
def _f_a_scopes(slice4_client):
    get_engine.cache_clear()
    _seed_ncvet_scope(NCVET_READER)
    yield slice4_client
    get_engine.cache_clear()


def test_happy_path_scoped_token_returns_evidence_and_audits(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_projection()
    resp = slice4_client.get(
        f"/v1/ledger/session_evidence/{SESSION_ID}",
        headers=_dev_auth(NCVET_READER),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == SESSION_ID
    assert body["grade_passed"] is True
    assert body["schema_version"] == "ncvet_session_evidence.v1"
    events = [e for e in audit.events if e.query_kind == "get_session_evidence"]
    assert events
    last = events[-1]
    assert last.outcome == "ok"
    assert last.result_row_count == 1
    assert last.actor_subject_id == NCVET_READER
    assert last.caller_kind == "ncvet_audit"
    assert last.subject_pseudo_id == "*"


def test_scope_deny_does_not_query_and_audits(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_projection()
    inner_calls: list[str] = []

    original = NcvetEvidenceReadService.get_session_evidence

    def _spy(self, session_id, *, auth, tenant_id=None):
        inner_calls.append(session_id)
        return original(self, session_id, auth=auth, tenant_id=tenant_id)

    monkeypatch.setattr(NcvetEvidenceReadService, "get_session_evidence", _spy)
    resp = slice4_client.get(
        f"/v1/ledger/session_evidence/{SESSION_ID}",
        headers=_dev_auth(NO_NCVET_SCOPE),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "scope_denied"
    assert inner_calls == []
    denied = [e for e in audit.events if e.outcome == "scope_denied"]
    assert len(denied) == 1
    assert denied[0].error_kind == "consent_scope"
    assert denied[0].caller_kind == "ncvet_audit"


def test_tenant_mismatch_denies_with_audit(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    _seed_projection()
    resp = slice4_client.get(
        f"/v1/ledger/session_evidence/{SESSION_ID}?tenant_id={OTHER_TENANT}",
        headers=_dev_auth(NCVET_READER),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "tenant_mismatch"
    denied = [e for e in audit.events if e.error_kind == "tenant_mismatch"]
    assert len(denied) == 1
    assert denied[0].outcome == "scope_denied"
    assert denied[0].caller_kind == "ncvet_audit"


def test_fail_closed_sink_emit_attempted_no_evidence_data(slice4_client, monkeypatch):
    sink = _TrackingFailingSink()
    _patch_audit_sink(monkeypatch, sink)
    _seed_projection()
    before = render_prometheus_metrics()
    resp = slice4_client.get(
        f"/v1/ledger/session_evidence/{SESSION_ID}",
        headers=_dev_auth(NCVET_READER),
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error"] == "audit_unavailable"
    assert detail.get("correlation_id")
    assert "grade_total" not in resp.text
    assert len(sink.calls) == 1
    assert sink.calls[0].query_kind == "get_session_evidence"
    assert sink.calls[0].outcome == "ok"
    after = render_prometheus_metrics()
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


def test_not_found_emits_audit_row_without_serving_evidence(slice4_client, monkeypatch):
    """404 = missing projection; still audited (outcome=error, error_kind=not_found)."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.get(
        f"/v1/ledger/session_evidence/{SESSION_ID}",
        headers=_dev_auth(NCVET_READER),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "session evidence not found"
    events = [e for e in audit.events if e.query_kind == "get_session_evidence"]
    assert len(events) == 1
    assert events[0].outcome == "error"
    assert events[0].error_kind == "not_found"
    assert events[0].result_row_count is None
    assert events[0].caller_kind == "ncvet_audit"
    assert events[0].actor_subject_id == NCVET_READER


def test_grep_excluded_fields_catches_nested_keys_in_serialized_json():
    nested = json.dumps(
        {
            "case_context": {
                "items": [{"metadata": {"demographics": {"name": "hidden"}}}],
            }
        }
    )
    assert set(_grep_excluded_field_names(nested)) == {"demographics", "name"}


def test_projection_excludes_demographics_and_free_text_by_grep(slice4_client, monkeypatch):
    _patch_audit_sink(monkeypatch, InMemoryAuditSink())
    _seed_projection()
    resp = slice4_client.get(
        f"/v1/ledger/session_evidence/{SESSION_ID}",
        headers=_dev_auth(NCVET_READER),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.text
    hits = _grep_excluded_field_names(payload)
    assert hits == [], f"excluded field names found in response JSON: {hits}"


def test_projection_read_hits_projection_table_not_ledger():
    """I-F-1: read path queries ``session_evidence_projection``, not ``session_ledger``."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    session.add(
        SessionEvidenceProjectionRow(
            session_id=SESSION_ID,
            tenant_id=TENANT,
            learner_pseudo_id="learner-pseudo-1",
            cohort_id="cohort-a",
            case_id="C1",
            case_version="1.0.0",
            finalized_at_utc=NOW,
            physio_engine_version="physio@v1",
            rubric_version="0.3.0",
            replay_hash="replay-hash-f-a",
            grade_total=0.85,
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

    touched: list[str] = []
    original_scalars = session.scalars

    def _spy_scalars(statement, *args, **kwargs):
        sql = str(statement).lower()
        if "session_evidence_projection" in sql:
            touched.append("projection")
        if "session_ledger" in sql:
            touched.append("ledger")
        return original_scalars(statement, *args, **kwargs)

    auth = AuthContext(
        subject_pseudo_id=NCVET_READER,
        tenant_id=TENANT,
        jti="j",
        issued_at=NOW,
        expires_at=NOW,
        source="dev",
    )
    svc = NcvetEvidenceReadService(session)
    with patch.object(session, "scalars", side_effect=_spy_scalars):
        view = svc.get_session_evidence(SESSION_ID, auth=auth)
    assert view.session_id == SESSION_ID
    assert "projection" in touched
    assert "ledger" not in touched

    import ast
    import services.pratibimb.ledger_read.ncvet_service as ncvet_mod

    tree = ast.parse(Path(ncvet_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "ledger.models" in node.module:
                imported = {alias.name for alias in node.names}
                assert "SessionLedgerRow" not in imported


def test_ensure_runtime_namespace_postgres_does_not_attach_sqlite():
    """Cross-dialect guard: ATTACH is SQLite-only; Postgres uses CREATE SCHEMA."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from services.pratibimb.ledger.namespace import ensure_runtime_namespace

    pg_conn = MagicMock()
    pg = MagicMock()
    pg.dialect.name = "postgresql"

    @contextmanager
    def _pg_begin():
        yield pg_conn

    pg.begin.side_effect = lambda: _pg_begin()
    ensure_runtime_namespace(pg)
    pg_conn.execute.assert_called_once()
    sql = str(pg_conn.execute.call_args[0][0])
    assert "CREATE SCHEMA" in sql
    assert "ATTACH" not in sql

    sqlite_conn = MagicMock()
    sqlite = MagicMock()
    sqlite.dialect.name = "sqlite"
    sqlite.url.database = "/tmp/ledger_test.db"

    @contextmanager
    def _sqlite_begin():
        yield sqlite_conn

    pragma_result = MagicMock()
    pragma_result.fetchall.return_value = [(0, "main", "/tmp/ledger_test.db")]
    sqlite.begin.side_effect = lambda: _sqlite_begin()
    sqlite_conn.execute.side_effect = [pragma_result, None]
    ensure_runtime_namespace(sqlite)
    attach_sql = str(sqlite_conn.execute.call_args_list[-1][0][0])
    assert "ATTACH DATABASE" in attach_sql
    assert "runtime" in attach_sql


def test_grading_finalize_path_does_not_import_ncvet_modules():
    """I-F-6 grading isolation — append/finalize must not reach NCVET gateway."""
    grading_paths = [
        _REPO_ROOT / "services" / "pratibimb" / "ledger" / "writer.py",
        _REPO_ROOT / "services" / "pratibimb" / "app" / "session" / "manager.py",
        _REPO_ROOT / "services" / "pratibimb" / "app" / "worker.py",
    ]
    forbidden = (
        "ncvet_gateway",
        "NcvetEvidenceReadService",
        "AuditingNcvetReadService",
        "JwtNcvetGateway",
    )
    for path in grading_paths:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for token in forbidden:
            assert token not in text, f"{rel} must not reference {token}"


def test_f_ncvet_kinds_and_caller_kind_closed_enum():
    assert F_NCVET_QUERY_KINDS == frozenset({
        "get_session_evidence",
        "list_learner_sessions",
    })
    assert F_NCVET_QUERY_KINDS <= KNOWN_QUERY_KINDS
    assert "ncvet_audit" in KNOWN_AUDIT_CALLER_KINDS

    with pytest.raises(ValueError, match="KNOWN_AUDIT_CALLER_KINDS"):
        assert_known_caller_kind("ncvet-audit")

    class _BadNcvetAudit(AuditingNcvetReadService):
        _REGISTERED_KINDS = frozenset({"bogus_ncvet_kind"})

    with pytest.raises(RuntimeError, match="F_NCVET_QUERY_KINDS"):
        _BadNcvetAudit(
            NcvetEvidenceReadService(None),  # type: ignore[arg-type]
            InMemoryAuditSink(),
        )


def test_fail_closed_propagates_audit_write_error_not_swallowed():
    from datetime import timedelta

    from shared.schemas.ledger_read import NcvetSessionEvidenceView

    class _StubInner(NcvetEvidenceReadService):
        def get_session_evidence(self, session_id, *, auth, tenant_id=None):
            return NcvetSessionEvidenceView(
                session_id=session_id,
                tenant_id=TENANT,
                learner_pseudo_id="L1",
                cohort_id="c1",
                case_id="C1",
                case_version="1.0.0",
                finalized_at_utc=NOW,
                physio_engine_version="v1",
                rubric_version="r1",
                replay_hash="h",
                grade_total=1.0,
                grade_passed=True,
                axis_normalized={},
                evidence=[],
                flags=[],
                actions=[],
                case_context={},
            )

    sink = _TrackingFailingSink()
    svc = AuditingNcvetReadService(_StubInner(None), sink)  # type: ignore[arg-type]
    auth = AuthContext(
        subject_pseudo_id=NCVET_READER,
        tenant_id=TENANT,
        jti="j",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        source="dev",
    )

    async def _call():
        return await svc.get_session_evidence(
            SESSION_ID,
            scope=ConsentScope.NCVET_READ_SESSION_EVIDENCE,
            auth=auth,
        )

    with pytest.raises(AuditWriteError):
        asyncio.run(_call())
    assert len(sink.calls) == 1
    assert sink.calls[0].caller_kind == "ncvet_audit"

    event = AuditEvent(
        query_id="q1",
        at_utc=NOW,
        tenant_id=TENANT,
        subject_pseudo_id="*",
        actor_subject_id=NCVET_READER,
        caller_kind="ncvet_audit",
        scope=ConsentScope.NCVET_READ_SESSION_EVIDENCE.value,
        query_kind="get_session_evidence",
        query_params_hash="abc",
        query_params_bytes=3,
        outcome="ok",
        duration_ms=1,
        result_row_count=1,
    )
    validate_audit_event(event)

    bad_event = AuditEvent(
        query_id="q2",
        at_utc=NOW,
        tenant_id=TENANT,
        subject_pseudo_id="*",
        actor_subject_id=None,
        caller_kind="ncvet_audit",
        scope=ConsentScope.NCVET_READ_SESSION_EVIDENCE.value,
        query_kind="get_session_evidence",
        query_params_hash="abc",
        query_params_bytes=3,
        outcome="ok",
        duration_ms=1,
    )
    with pytest.raises(ValueError, match="actor_subject_id"):
        validate_audit_event(bad_event)
