"""H.a: regrade E2E — 15-case acceptance matrix."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

pytestmark = pytest.mark.h_acceptance

from services.pratibimb.audit.caller_kinds import (
    KNOWN_AUDIT_CALLER_KINDS,
    assert_known_caller_kind,
)
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.ledger.db import get_engine
from services.pratibimb.ledger.models import RegradeArtifactRow, RegradeAuditRow
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS
from services.pratibimb.regrade.digest import DIGEST_ALG_PLACEHOLDER, compute_transcript_digest
from services.pratibimb.regrade.sign import (
    DEFAULT_REGRADE_ISSUER_KEY_ID,
    build_portable_envelope,
    verify_regrade_signature,
)
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    OTHER_TENANT,
    _dev_auth,
    slice4_client,
)
from shared.schemas.ledger_read import ConsentScope

REGRADE = "ncvet-regrader-h-a-1"
ISSUER = "ncvet-issuer-h-a-2"
VERIFIER = "ncvet-verifier-h-a-3"
NO_SCOPE = "no-regrade-scope-h-a-4"
SESSION_ID = "sess-regrade-h-a-1"
_REPO_ROOT = Path(__file__).resolve().parents[3]
UTC = timezone.utc
NOW = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)
_F_LIVE = frozenset({"get_session_evidence", "list_learner_sessions"})
_REGRADE_ROOT = _REPO_ROOT / "services" / "pratibimb" / "regrade"


def _seed_scope(subject: str, scope: ConsentScope, tenant: str = TENANT) -> None:
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store

    asyncio.run(
        seed_authoring_scope(
            tenant_id=tenant,
            subject_id=subject,
            scope=scope,
            store=get_in_memory_consent_store(),
        )
    )


def _seed_transcript(
    *,
    session_id: str = SESSION_ID,
    tenant_id: str = TENANT,
    rubric: str = "rubric.v1",
    tamper_digest: bool = False,
    payload: dict | None = None,
) -> dict:
    from services.pratibimb.ledger.db import init_ledger_schema
    from services.pratibimb.ledger.models import SessionTranscriptProjectionRow

    payload = payload or {
        "actions": [{"id": "a1", "ok": True}],
        "case_id": "case-1",
        "rubric_schema_version": rubric,
        "note": "sealed-bytes",
    }
    digest, _ = compute_transcript_digest(payload)
    stored_digest = "deadbeef" * 8 if tamper_digest else digest
    get_engine.cache_clear()
    engine = get_engine()
    init_ledger_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        existing = session.get(SessionTranscriptProjectionRow, session_id)
        if existing:
            session.delete(existing)
            session.flush()
        session.add(
            SessionTranscriptProjectionRow(
                session_id=session_id,
                tenant_id=tenant_id,
                transcript_digest=stored_digest,
                rubric_schema_version=rubric,
                payload_json=json.dumps(payload, sort_keys=True),
                projected_at=NOW,
            )
        )
        session.commit()
    return payload


@pytest.fixture(autouse=True)
def _h_a_seed(slice4_client):
    _seed_scope(REGRADE, ConsentScope.NCVET_REGRADE_SESSION)
    _seed_scope(ISSUER, ConsentScope.NCVET_ISSUE_CREDENTIAL)
    _seed_scope(VERIFIER, ConsentScope.NCVET_VERIFY_CREDENTIAL)
    _seed_transcript()
    yield


def test_h_a_1_happy_regrade(slice4_client):
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["regrade_id"]
    assert body["grade_payload"]
    assert body["proof"]["signature"]
    assert body["proof"]["regrade_issuer_key_id"] == DEFAULT_REGRADE_ISSUER_KEY_ID
    assert body["rubric_schema_version"] == "rubric.v1"
    assert body["digest_alg"] == DIGEST_ALG_PLACEHOLDER
    assert verify_regrade_signature(body)


def test_h_a_portable_envelope_excludes_session_id_by_grep(slice4_client):
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 200
    assert not re.search(r'"session_id"\s*:', resp.text), resp.text
    assert not re.search(r'"learner_pseudo_id"\s*:', resp.text), resp.text
    body = resp.json()
    assert "session_id" not in json.dumps(body)


def test_h_a_3_sealed_rubric_not_live(slice4_client):
    _seed_transcript(rubric="rubric.v1")
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 200
    assert resp.json()["rubric_schema_version"] == "rubric.v1"
    assert resp.json()["grade_payload"]["rubric_schema_version"] == "rubric.v1"
    live_rubric = "rubric.v2"
    assert live_rubric != resp.json()["rubric_schema_version"]


def test_h_a_4_digest_mismatch_no_artifact(slice4_client):
    _seed_transcript(tamper_digest=True)
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_kind"] == "transcript_digest_mismatch"
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        arts = list(session.execute(select(RegradeArtifactRow)).scalars())
        assert arts == []
        audits = list(session.execute(select(RegradeAuditRow)).scalars())
        assert len(audits) == 1
        assert audits[0].grader_outcome == "not_invoked"
        assert audits[0].error_kind == "transcript_digest_mismatch"
        assert audits[0].digest_match is False


def test_h_a_5_scope_deny(slice4_client):
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(NO_SCOPE),
    )
    assert resp.status_code == 403
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        assert list(session.execute(select(RegradeArtifactRow)).scalars()) == []


def test_h_a_6_g_scopes_do_not_imply_regrade(slice4_client):
    for subject in (ISSUER, VERIFIER):
        assert (
            slice4_client.post(
                f"/v1/regrade/session/{SESSION_ID}",
                headers=_dev_auth(subject),
            ).status_code
            == 403
        )


def test_h_a_7_tenant_deny(slice4_client):
    _seed_transcript(tenant_id=OTHER_TENANT)
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 403
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        assert list(session.execute(select(RegradeArtifactRow)).scalars()) == []


def test_h_a_8_fail_closed_audit_sink(slice4_client, monkeypatch):
    import services.pratibimb.regrade.routes as routes
    from services.pratibimb.app.main import app
    from services.pratibimb.ledger_read.auth import ConsentResolver
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store
    from services.pratibimb.regrade.gateway import JwtRegradeGateway
    from services.pratibimb.regrade.service import RegradeService

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)

    def _failing_gateway():
        return JwtRegradeGateway(
            RegradeService(SessionLocal, fail_audit=True),
            ConsentResolver(get_in_memory_consent_store()),
        )

    app.dependency_overrides[routes.get_regrade_gateway] = _failing_gateway
    try:
        resp = slice4_client.post(
            f"/v1/regrade/session/{SESSION_ID}",
            headers=_dev_auth(REGRADE),
        )
        assert resp.status_code == 503
        with SessionLocal() as session:
            assert list(session.execute(select(RegradeArtifactRow)).scalars()) == []
    finally:
        app.dependency_overrides.pop(routes.get_regrade_gateway, None)


def test_h_a_9_caller_kind_and_016_belt():
    assert "ncvet_regrader" in KNOWN_AUDIT_CALLER_KINDS
    assert_known_caller_kind("ncvet_regrader")
    with pytest.raises(ValueError, match="caller_kind"):
        assert_known_caller_kind("ncvet-regrader")
    sql = (
        _REPO_ROOT
        / "services"
        / "pratibimb"
        / "ledger"
        / "migrations"
        / "016_regrade_artifact.sql"
    ).read_text(encoding="utf-8")
    assert "ncvet_regrader" in sql
    assert "regrade_audit" in sql
    assert "regrade_artifacts" in sql


def test_h_a_10_audit_cardinality_per_attempt(slice4_client):
    _seed_transcript(tamper_digest=True)
    slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        audits = list(session.execute(select(RegradeAuditRow)).scalars())
        assert len(audits) == 2
        assert all(a.transcript_digest_expected for a in audits)
        assert all(a.transcript_digest_computed for a in audits)

    _seed_transcript(tamper_digest=False)
    ok = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert ok.status_code == 200
    with SessionLocal() as session:
        assert len(list(session.execute(select(RegradeAuditRow)).scalars())) == 3


def test_h_a_11_projection_only_no_grading_import():
    import ast

    for path in _REGRADE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "SessionTranscriptLedgerRow" not in text
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "grading" not in node.module.split("."), path.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "grading" not in alias.name.split("."), path.name


def test_h_a_12_grading_isolation_both_ways():
    import ast

    writer = _REPO_ROOT / "services" / "pratibimb" / "ledger" / "writer.py"
    text = writer.read_text(encoding="utf-8")
    assert "services.pratibimb.regrade" not in text
    for path in _REGRADE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("services.pratibimb.grading"), path.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("services.pratibimb.grading"), path.name


def test_h_a_13_no_new_f_ledger_kinds():
    assert F_NCVET_QUERY_KINDS == _F_LIVE


def test_h_a_14_sync_mvp_no_queue_imports():
    forbidden = ("celery", "asyncio.create_task", "BackgroundTasks")
    for path in _REGRADE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"


def test_h_a_15_wrong_key_signature_rejects():
    env = build_portable_envelope(
        regrade_id="r1",
        transcript_digest="abc",
        grade_payload={"grade_total": 0.9, "grade_passed": True},
        grader_version="sealed-stub.v1",
        rubric_schema_version="rubric.v1",
        digest_alg=DIGEST_ALG_PLACEHOLDER,
    )
    assert verify_regrade_signature(env)
    # Distinct key_id → different HMAC material (I-H-14 option b).
    assert verify_regrade_signature(env, key_id="other-regrade-key") is False
    # G credential issuer key_id must not verify regrade envelopes.
    assert verify_regrade_signature(env, key_id="dev-issuer-key-1") is False
    env["grade_payload"] = {"grade_total": 0.1}
    assert verify_regrade_signature(env) is False
