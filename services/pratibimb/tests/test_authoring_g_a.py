"""G.a: credential issue + evidence_ref fetch — 12-case acceptance matrix."""
from __future__ import annotations

import asyncio
import ast
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

from services.pratibimb.audit.caller_kinds import (
    KNOWN_AUDIT_CALLER_KINDS,
    assert_known_caller_kind,
)
from services.pratibimb.audit.sink import InMemoryAuditSink
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.credentials.digest import compute_projection_digest
from services.pratibimb.ledger.db import get_engine, init_ledger_schema
from services.pratibimb.ledger.models import CredentialLedgerRow, SessionEvidenceProjectionRow
from services.pratibimb.ledger_read.deps import reset_auth_wiring_cache
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS, KNOWN_QUERY_KINDS
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_f_a import (
    SESSION_ID,
    _TrackingFailingSink,
    _redacted_case_context,
    _seed_projection,
)
from services.pratibimb.tests.test_authoring_phase_d_slice4 import _dev_auth, slice4_client
from shared.schemas.ledger_read import ConsentScope

ISSUER = "ncvet-issuer-g-a-1"
FETCHER = "ncvet-fetcher-g-a-2"
NO_SCOPE = "no-cred-scope-g-a-3"
_REPO_ROOT = Path(__file__).resolve().parents[3]
UTC = timezone.utc
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)

# Frozen F live kinds — G.a must not extend (I-G-8 / I-F-2).
_F_LIVE_KINDS_FREEZE = frozenset({"get_session_evidence", "list_learner_sessions"})


def _patch_audit_sink(monkeypatch, audit) -> None:
    from services.pratibimb.ledger_read import deps as ledger_deps
    import services.pratibimb.ledger_read.routes as ledger_routes
    import services.pratibimb.credentials.routes as cred_routes

    ledger_deps.get_audit_sink.cache_clear()
    monkeypatch.setattr(ledger_deps, "get_audit_sink", lambda: audit)
    monkeypatch.setattr(ledger_routes, "get_audit_sink", lambda: audit)
    monkeypatch.setattr(cred_routes, "get_audit_sink", lambda: audit)
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
def _g_a_seed(slice4_client):
    _seed_projection()
    _seed_scope(ISSUER, ConsentScope.NCVET_ISSUE_CREDENTIAL)
    _seed_scope(FETCHER, ConsentScope.NCVET_FETCH_EVIDENCE_BY_REF)
    yield


def test_1_happy_issue_no_session_id_in_body(slice4_client, monkeypatch):
    import re

    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Recursive / wire-format grep — same discipline as F.a I-F-5 (not top-level only).
    assert not re.search(r'"session_id"\s*:', resp.text), resp.text
    assert "session_id" not in json.dumps(body)
    assert body.get("evidence_ref")
    assert body.get("projection_digest")
    assert body.get("proof", {}).get("signature")


def test_2_projection_binding_digest(slice4_client, monkeypatch):
    """Digest binds to F projection view fields — not SessionLedgerRow (I-G-3)."""
    from services.pratibimb.credentials import digest as digest_mod

    src = Path(digest_mod.__file__).read_text(encoding="utf-8")
    assert "SessionLedgerRow" not in src
    assert "session_ledger" not in src
    assert "NcvetSessionEvidenceView" in src

    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert resp.status_code == 200
    digest = resp.json()["projection_digest"]
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.execute(select(CredentialLedgerRow)).scalar_one()
        assert row.projection_digest == digest
        assert row.session_id == SESSION_ID


def test_3_audit_chain_ncvet_issuer(slice4_client, monkeypatch):
    """One ledger_read_audit (ncvet_issuer + get_session_evidence) + credential_ledger link."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert resp.status_code == 200
    issuer_reads = [
        e
        for e in audit.events
        if e.caller_kind == "ncvet_issuer" and e.query_kind == "get_session_evidence"
    ]
    assert len(issuer_reads) == 1
    assert issuer_reads[0].outcome == "ok"
    assert issuer_reads[0].result_fingerprint is not None
    # Negative-space: issuer path must not bleed ncvet_audit (distinct closed-enum value).
    kinds = {e.caller_kind for e in audit.events}
    assert kinds <= {"ncvet_issuer"}
    assert "ncvet_audit" not in kinds
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.execute(select(CredentialLedgerRow)).scalar_one()
        assert row.audit_query_id == issuer_reads[0].query_id
        assert row.audit_fingerprint == issuer_reads[0].result_fingerprint


def test_4_scope_deny_issue(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(NO_SCOPE),
        json={"session_id": SESSION_ID},
    )
    assert resp.status_code == 403
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        assert session.execute(select(CredentialLedgerRow)).scalars().all() == []


def test_5_fail_closed_no_ledger_row(slice4_client, monkeypatch):
    sink = _TrackingFailingSink()
    _patch_audit_sink(monkeypatch, sink)
    resp = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "audit_unavailable"
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        assert session.execute(select(CredentialLedgerRow)).scalars().all() == []


def test_g_a_four_way_isolation_by_import_grep():
    """I-G-4: credentials package must not import grading / authoring draft / projection ORM."""
    cred_root = _REPO_ROOT / "services" / "pratibimb" / "credentials"
    forbidden_names = frozenset(
        {
            "SessionEvidenceProjectionRow",
            "CaseDraftStore",
            "append_graded_session",
        }
    )
    forbidden_modules = (
        "services.pratibimb.app.session",
        "services.pratibimb.grading",
        "services.pratibimb.authoring.store",
    )
    hits: list[str] = []
    for path in cred_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = {a.name for a in node.names}
                if names & forbidden_names:
                    hits.append(
                        f"{path.name}: from {node.module} import {names & forbidden_names}"
                    )
                if node.module in forbidden_modules:
                    hits.append(f"{path.name}: import {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        hits.append(f"{path.name}: import {alias.name}")
    assert hits == [], hits


def test_7_closed_caller_kind_ncvet_issuer():
    assert "ncvet_issuer" in KNOWN_AUDIT_CALLER_KINDS
    assert_known_caller_kind("ncvet_issuer")
    # G.b landed ncvet_verifier in the same closed enum (015).
    assert_known_caller_kind("ncvet_verifier")
    with pytest.raises(ValueError, match="caller_kind"):
        assert_known_caller_kind("ncvet-issuer")


def test_8_no_new_ledger_query_kinds():
    assert F_NCVET_QUERY_KINDS == _F_LIVE_KINDS_FREEZE
    assert "issue_credential" not in KNOWN_QUERY_KINDS
    assert "get_evidence_by_digest" not in KNOWN_QUERY_KINDS


def test_9_evidence_ref_opacity(slice4_client, monkeypatch):
    from services.pratibimb.credentials.service import EVIDENCE_REF_ENTROPY_BYTES

    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert resp.status_code == 200
    ref = resp.json()["evidence_ref"]
    assert ref != SESSION_ID
    guess = hashlib.sha256(f"{SESSION_ID}|{resp.json()['credential_id']}".encode()).hexdigest()
    assert ref != guess
    assert SESSION_ID not in ref
    # Entropy pin: token_urlsafe(N) → ~4/3 * N chars; N=32 → ≥43 urlsafe chars (256-bit).
    assert EVIDENCE_REF_ENTROPY_BYTES == 32  # guessability threshold — do not lower without review
    assert len(ref) >= 43


def test_10_fetch_by_ref(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    issued = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert issued.status_code == 200
    ref = issued.json()["evidence_ref"]

    denied = slice4_client.get(
        f"/v1/credentials/evidence/{ref}",
        headers=_dev_auth(ISSUER),  # issue scope only — not fetch
    )
    assert denied.status_code == 403

    ok = slice4_client.get(
        f"/v1/credentials/evidence/{ref}",
        headers=_dev_auth(FETCHER),
    )
    assert ok.status_code == 200
    assert ok.json()["session_id"] == SESSION_ID

    # Revoke ref independently
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.execute(select(CredentialLedgerRow)).scalar_one()
        row.evidence_ref_revoked_at = NOW
        session.commit()

    gone = slice4_client.get(
        f"/v1/credentials/evidence/{ref}",
        headers=_dev_auth(FETCHER),
    )
    assert gone.status_code == 404
    assert gone.json()["detail"]["error_kind"] == "ref_revoked"


def test_11_grading_isolation_no_credentials_import():
    writer = _REPO_ROOT / "services" / "pratibimb" / "ledger" / "writer.py"
    text = writer.read_text(encoding="utf-8")
    assert "services.pratibimb.credentials" not in text
    assert "credential_ledger" not in text.lower() or "CredentialLedger" not in text


def test_12_reissue_rotates_ref(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    first = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert first.status_code == 200
    ref1 = first.json()["evidence_ref"]
    second = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert second.status_code == 200
    ref2 = second.json()["evidence_ref"]
    assert ref1 != ref2
    assert first.json()["credential_id"] == second.json()["credential_id"]
    assert second.json()["credential_version"] == 2

    old = slice4_client.get(
        f"/v1/credentials/evidence/{ref1}",
        headers=_dev_auth(FETCHER),
    )
    assert old.status_code == 404
    assert old.json()["detail"]["error_kind"] == "ref_revoked"
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        rows = list(
            session.execute(
                select(CredentialLedgerRow).order_by(CredentialLedgerRow.credential_version)
            ).scalars()
        )
        assert len(rows) == 2
        assert rows[0].evidence_ref_revoked_at is not None
        assert rows[0].evidence_ref_revoked_at >= rows[0].issued_at
        assert rows[1].evidence_ref_revoked_at is None
    new = slice4_client.get(
        f"/v1/credentials/evidence/{ref2}",
        headers=_dev_auth(FETCHER),
    )
    assert new.status_code == 200
