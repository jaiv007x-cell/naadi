"""G.b: offline verify + status list + revoke — 22-case acceptance matrix."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
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
from services.pratibimb.credentials.sign import DEFAULT_ISSUER_KEY_ID, sign_credential_claims
from services.pratibimb.credentials.status_list import (
    DEFAULT_MAX_STALENESS,
    KEY_DEPRECATION_WINDOW,
    STATUS_LIST_SCHEMA_V1,
    build_status_list_envelope,
)
from services.pratibimb.credentials.status_service import STATUS_LIST_PAGE_CAP
from services.pratibimb.credentials.verify import KeyRecord, VerifyReject, offline_verify
from services.pratibimb.ledger.db import get_engine
from services.pratibimb.ledger.models import CredentialLedgerRow
from services.pratibimb.ledger_read.deps import reset_auth_wiring_cache
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS, KNOWN_QUERY_KINDS
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_f_a import SESSION_ID, _seed_projection
from services.pratibimb.tests.test_authoring_phase_d_slice4 import _dev_auth, slice4_client
from shared.schemas.ledger_read import ConsentScope

ISSUER = "ncvet-issuer-g-b-1"
VERIFIER = "ncvet-verifier-g-b-2"
FETCHER = "ncvet-status-g-b-3"
REVOKER = "ncvet-revoker-g-b-4"
ONLY_VERIFY = "ncvet-only-verify-g-b-5"
ONLY_STATUS = "ncvet-only-status-g-b-6"
ONLY_REVOKE = "ncvet-only-revoke-g-b-7"

_REPO_ROOT = Path(__file__).resolve().parents[3]
UTC = timezone.utc
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
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


def _fresh_list(*, entries=None, signed_at=None, valid_until=None, key_id=DEFAULT_ISSUER_KEY_ID):
    signed_at = signed_at or NOW
    valid_until = valid_until or (NOW + timedelta(days=30))
    return build_status_list_envelope(
        entries=entries or [],
        key_id=key_id,
        signed_at=signed_at,
        valid_until=valid_until,
    )


def _cred(credential_id: str = "cred-1", **extra):
    claims = {
        "type": ["VerifiableCredential", "NaadiCompetencyCredential"],
        "credential_id": credential_id,
        "credential_version": 1,
        "evidence_ref": "opaque-ref",
        "projection_digest": "abc",
        "digest_alg": "sha256",
        "issued_at": NOW.isoformat(),
        "issuer_key_id": DEFAULT_ISSUER_KEY_ID,
        "tenant_id": TENANT,
        **extra,
    }
    return sign_credential_claims(claims, key_id=DEFAULT_ISSUER_KEY_ID)


def _keyring(**overrides):
    base = {DEFAULT_ISSUER_KEY_ID: KeyRecord(key_id=DEFAULT_ISSUER_KEY_ID)}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _g_b_seed(slice4_client):
    _seed_projection()
    _seed_scope(ISSUER, ConsentScope.NCVET_ISSUE_CREDENTIAL)
    _seed_scope(VERIFIER, ConsentScope.NCVET_VERIFY_CREDENTIAL)
    _seed_scope(FETCHER, ConsentScope.NCVET_FETCH_STATUS_LIST)
    _seed_scope(REVOKER, ConsentScope.NCVET_REVOKE_CREDENTIAL)
    _seed_scope(ONLY_VERIFY, ConsentScope.NCVET_VERIFY_CREDENTIAL)
    _seed_scope(ONLY_STATUS, ConsentScope.NCVET_FETCH_STATUS_LIST)
    _seed_scope(ONLY_REVOKE, ConsentScope.NCVET_REVOKE_CREDENTIAL)
    yield


def test_g_b_1_offline_verify_happy():
    cred = _cred()
    status = _fresh_list()
    result = offline_verify(cred, status, keyring=_keyring(), now=NOW)
    assert result.accepted
    assert result.warnings == ()


def test_g_b_verify_assist_endpoint_agrees_with_offline_verdict(
    slice4_client, monkeypatch
):
    """Assist convenience must not drift from offline machine semantics."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    issued = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert issued.status_code == 200
    cred = issued.json()

    # Accept path
    status_ok = _fresh_list()
    offline_ok = offline_verify(cred, status_ok, keyring=_keyring(), now=NOW)
    assist_ok = slice4_client.post(
        "/v1/credentials/verify",
        headers=_dev_auth(VERIFIER),
        json={"credential": cred, "status_list": status_ok},
    )
    assert assist_ok.status_code == 200
    body_ok = assist_ok.json()
    assert body_ok["accepted"] is offline_ok.accepted is True
    assert body_ok["reason"] == offline_ok.reason

    # Reject path (revoked) — same verdict both surfaces
    rev = slice4_client.post(
        "/v1/credentials/revoke",
        headers=_dev_auth(REVOKER),
        json={"credential_id": cred["credential_id"]},
    )
    assert rev.status_code == 200
    status_rev = rev.json()["status_list"]
    assert status_rev["status_list_schema_version"] == "status_list.v2"
    assert status_rev["entries"]
    assert all(
        e.get("identifier_kind") == "credential" and e.get("identifier_id")
        for e in status_rev["entries"]
    )
    with pytest.raises(VerifyReject, match="credential_revoked"):
        offline_verify(cred, status_rev, keyring=_keyring(), now=NOW)
    assist_rej = slice4_client.post(
        "/v1/credentials/verify",
        headers=_dev_auth(VERIFIER),
        json={"credential": cred, "status_list": status_rev},
    )
    assert assist_rej.status_code == 200
    assert assist_rej.json()["accepted"] is False
    assert assist_rej.json()["reason"] == "credential_revoked"


def test_g_b_revoking_credential_does_invalidate_future_verifications(
    slice4_client, monkeypatch
):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    issued = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert issued.status_code == 200
    cred = issued.json()
    cid = cred["credential_id"]

    before = offline_verify(cred, _fresh_list(), keyring=_keyring(), now=NOW)
    assert before.accepted

    rev = slice4_client.post(
        "/v1/credentials/revoke",
        headers=_dev_auth(REVOKER),
        json={"credential_id": cid},
    )
    assert rev.status_code == 200
    status = rev.json()["status_list"]
    with pytest.raises(VerifyReject, match="credential_revoked"):
        offline_verify(cred, status, keyring=_keyring(), now=NOW)


def test_g_b_3_tampered_credential_signature_fails():
    cred = _cred()
    cred["projection_digest"] = "tampered"
    with pytest.raises(VerifyReject, match="credential_bad_signature"):
        offline_verify(cred, _fresh_list(), keyring=_keyring(), now=NOW)


def test_g_b_4_tampered_status_list_rejects():
    status = _fresh_list()
    status["entries"] = [{"credential_id": "x"}]
    with pytest.raises(VerifyReject, match="status_list_bad_signature"):
        offline_verify(_cred(), status, keyring=_keyring(), now=NOW)


def test_g_b_5_list_past_valid_until_rejects_even_under_option_b():
    status = _fresh_list(
        signed_at=NOW - timedelta(days=1),
        valid_until=NOW - timedelta(hours=1),
    )
    with pytest.raises(VerifyReject, match="status_list_expired"):
        offline_verify(
            _cred(),
            status,
            keyring=_keyring(),
            now=NOW,
            option_b_staleness=True,
            option_b_reason="incident",
        )


def test_g_b_valid_until_le_signed_at_rejects_pre_signature_check():
    # Build unsigned nonsense then attach a valid-looking proof — check must fire first.
    payload = {
        "status_list_schema_version": STATUS_LIST_SCHEMA_V1,
        "key_id": DEFAULT_ISSUER_KEY_ID,
        "signed_at": NOW.isoformat(),
        "valid_until": (NOW - timedelta(seconds=1)).isoformat(),
        "entries": [],
        "proof": {
            "type": "JsonHmac2026",
            "key_id": DEFAULT_ISSUER_KEY_ID,
            "signature": "deadbeef",
        },
    }
    with pytest.raises(VerifyReject, match="valid_until_le_signed_at"):
        offline_verify(_cred(), payload, keyring=_keyring(), now=NOW)


def test_g_b_7_max_staleness_option_a_hard_fails():
    signed = NOW - DEFAULT_MAX_STALENESS - timedelta(hours=1)
    status = _fresh_list(signed_at=signed, valid_until=NOW + timedelta(days=20))
    with pytest.raises(VerifyReject, match="status_list_stale"):
        offline_verify(_cred(), status, keyring=_keyring(), now=NOW)


def test_g_b_staleness_option_b_override_requires_per_call_flag_and_emits_audit(
    caplog,
):
    signed = NOW - DEFAULT_MAX_STALENESS - timedelta(hours=1)
    status = _fresh_list(signed_at=signed, valid_until=NOW + timedelta(days=20))
    # Sticky-style call without flag → hard fail
    with pytest.raises(VerifyReject, match="status_list_stale"):
        offline_verify(_cred(), status, keyring=_keyring(), now=NOW)
    with pytest.raises(VerifyReject, match="option_b_requires_reason"):
        offline_verify(
            _cred(),
            status,
            keyring=_keyring(),
            now=NOW,
            option_b_staleness=True,
            option_b_reason="",
        )
    with caplog.at_level(logging.WARNING, logger="credentials.verify"):
        result = offline_verify(
            _cred(),
            status,
            keyring=_keyring(),
            now=NOW,
            option_b_staleness=True,
            option_b_reason="airgap-incident",
        )
    assert result.accepted
    assert "stale_status_list" in result.warnings
    assert any("AUDIT option_b_staleness" in r.message for r in caplog.records)


def test_g_b_9_ncvet_verifier_accepted_unknown_rejected():
    assert "ncvet_verifier" in KNOWN_AUDIT_CALLER_KINDS
    assert_known_caller_kind("ncvet_verifier")
    with pytest.raises(ValueError, match="caller_kind"):
        assert_known_caller_kind("ncvet-verifier")


def test_g_b_ncvet_verifier_caller_kind_unknown_at_app_enum_raises():
    with pytest.raises(ValueError, match="caller_kind"):
        assert_known_caller_kind("ncvet_verifie")


def test_g_b_11_015_sql_belt_includes_ncvet_verifier():
    sql = (
        _REPO_ROOT
        / "services"
        / "pratibimb"
        / "ledger"
        / "migrations"
        / "015_credential_status_list.sql"
    ).read_text(encoding="utf-8")
    assert "ncvet_verifier" in sql
    assert "ck_audit_caller_kind" in sql
    assert "credential_status_list_snapshot" in sql


def test_g_b_12_status_list_http_emits_ncvet_verifier(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    # Seed a snapshot via revoke
    issued = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert issued.status_code == 200
    slice4_client.post(
        "/v1/credentials/revoke",
        headers=_dev_auth(REVOKER),
        json={"credential_id": issued.json()["credential_id"]},
    ).raise_for_status()

    resp = slice4_client.get(
        "/v1/credentials/status_list",
        headers=_dev_auth(FETCHER),
    )
    assert resp.status_code == 200
    verifier_events = [e for e in audit.events if e.caller_kind == "ncvet_verifier"]
    assert len(verifier_events) >= 1
    assert verifier_events[-1].query_kind == "fetch_credential_status_list"
    assert verifier_events[-1].outcome == "ok"


def test_g_b_scope_split_matrix_issue_verify_fetch_are_disjoint(slice4_client, monkeypatch):
    """Route-level six-cell: verify / fetch_status / revoke mutually disjoint."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    issued = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert issued.status_code == 200
    cred = issued.json()
    status = _fresh_list()

    # ONLY_VERIFY: verify OK; status + revoke denied
    assert (
        slice4_client.post(
            "/v1/credentials/verify",
            headers=_dev_auth(ONLY_VERIFY),
            json={"credential": cred, "status_list": status},
        ).status_code
        == 200
    )
    assert (
        slice4_client.get(
            "/v1/credentials/status_list",
            headers=_dev_auth(ONLY_VERIFY),
        ).status_code
        == 403
    )
    assert (
        slice4_client.post(
            "/v1/credentials/revoke",
            headers=_dev_auth(ONLY_VERIFY),
            json={"credential_id": cred["credential_id"]},
        ).status_code
        == 403
    )

    # ONLY_STATUS: status OK; verify + revoke denied
    assert (
        slice4_client.get(
            "/v1/credentials/status_list",
            headers=_dev_auth(ONLY_STATUS),
        ).status_code
        == 200
    )
    assert (
        slice4_client.post(
            "/v1/credentials/verify",
            headers=_dev_auth(ONLY_STATUS),
            json={"credential": cred, "status_list": status},
        ).status_code
        == 403
    )
    assert (
        slice4_client.post(
            "/v1/credentials/revoke",
            headers=_dev_auth(ONLY_STATUS),
            json={"credential_id": cred["credential_id"]},
        ).status_code
        == 403
    )

    # ONLY_REVOKE: revoke OK; verify + status denied
    assert (
        slice4_client.post(
            "/v1/credentials/verify",
            headers=_dev_auth(ONLY_REVOKE),
            json={"credential": cred, "status_list": status},
        ).status_code
        == 403
    )
    assert (
        slice4_client.get(
            "/v1/credentials/status_list",
            headers=_dev_auth(ONLY_REVOKE),
        ).status_code
        == 403
    )
    assert (
        slice4_client.post(
            "/v1/credentials/revoke",
            headers=_dev_auth(ONLY_REVOKE),
            json={"credential_id": cred["credential_id"]},
        ).status_code
        == 200
    )


def test_g_b_verifier_scopes_do_not_grant_issuer_capabilities(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    for subject in (ONLY_VERIFY, ONLY_STATUS, ONLY_REVOKE):
        assert (
            slice4_client.post(
                "/v1/credentials/issue",
                headers=_dev_auth(subject),
                json={"session_id": SESSION_ID},
            ).status_code
            == 403
        )
    # Issuer cannot hit G.b verifier routes
    assert (
        slice4_client.get(
            "/v1/credentials/status_list",
            headers=_dev_auth(ISSUER),
        ).status_code
        == 403
    )
    assert (
        slice4_client.post(
            "/v1/credentials/verify",
            headers=_dev_auth(ISSUER),
            json={"credential": _cred(), "status_list": _fresh_list()},
        ).status_code
        == 403
    )


def test_g_b_15_status_list_pagination_cursor_and_cap(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    from services.pratibimb.credentials.status_service import CredentialStatusService

    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    # Empty snapshots collide if signed_at is identical — advance clock so
    # envelope bytes (and pagination content) are distinguishable.
    tick = {"n": 0}

    def _now():
        tick["n"] += 1
        return NOW + timedelta(seconds=tick["n"])

    svc = CredentialStatusService(SessionLocal, audit, now=_now)
    with SessionLocal() as session:
        for _ in range(STATUS_LIST_PAGE_CAP + 2):
            svc._publish_snapshot(session, tenant_id=TENANT)
        session.commit()

    page1 = slice4_client.get(
        "/v1/credentials/status_list",
        headers=_dev_auth(FETCHER),
        params={"limit": 5},
    )
    assert page1.status_code == 200
    body = page1.json()
    assert body["page_cap"] == STATUS_LIST_PAGE_CAP
    assert len(body["items"]) == 5
    assert body["next_cursor"]
    page2 = slice4_client.get(
        "/v1/credentials/status_list",
        headers=_dev_auth(FETCHER),
        params={"limit": 5, "cursor": body["next_cursor"]},
    )
    assert page2.status_code == 200
    assert len(page2.json()["items"]) >= 1
    # Cursor advanced past first page's last snapshot
    assert page2.json()["items"][0] != body["items"][0]
    # P1: mint path emits v2 with kind/id shape (empty list still stamped v2)
    assert body["items"][0]["status_list_schema_version"] == "status_list.v2"


def test_g_b_16_entry_minimization_no_session_or_evidence_ref():
    status = _fresh_list(
        entries=[{"credential_id": "c1", "revoked_at": NOW.isoformat()}]
    )
    blob = json.dumps(status)
    assert "session_id" not in blob
    assert "evidence_ref" not in blob
    for e in status["entries"]:
        assert set(e.keys()) <= {"credential_id", "revoked_at"}


def test_g_b_revoking_evidence_ref_does_not_invalidate_prior_verifications(
    slice4_client, monkeypatch
):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    issued = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert issued.status_code == 200
    cred = issued.json()
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.execute(select(CredentialLedgerRow)).scalar_one()
        row.evidence_ref_revoked_at = NOW
        assert row.revoked_at is None
        session.commit()
    result = offline_verify(cred, _fresh_list(), keyring=_keyring(), now=NOW)
    assert result.accepted


def test_g_b_18_no_new_ledger_query_kinds():
    assert F_NCVET_QUERY_KINDS == _F_LIVE_KINDS_FREEZE
    assert "issue_credential" not in KNOWN_QUERY_KINDS
    # Credentials-surface kind is allowed; must not be an F ledger kind.
    assert "fetch_credential_status_list" in KNOWN_QUERY_KINDS
    assert "fetch_credential_status_list" not in F_NCVET_QUERY_KINDS


def test_g_b_19_key_rotation_without_resign_old_list_verifies():
    old_key = "issuer-key-retired-shape"
    status = _fresh_list(key_id=old_key)
    kr = {
        old_key: KeyRecord(key_id=old_key),
        DEFAULT_ISSUER_KEY_ID: KeyRecord(key_id=DEFAULT_ISSUER_KEY_ID),
    }
    result = offline_verify(_cred(), status, keyring=kr, now=NOW)
    assert result.accepted


def test_g_b_retired_key_within_deprecation_window_verifies_with_warning():
    retired_at = NOW - timedelta(days=30)  # within 90d window
    kr = {
        DEFAULT_ISSUER_KEY_ID: KeyRecord(
            key_id=DEFAULT_ISSUER_KEY_ID, retired_at=retired_at
        )
    }
    result = offline_verify(_cred(), _fresh_list(), keyring=kr, now=NOW)
    assert result.accepted
    assert "retired_key_within_window" in result.warnings


def test_g_b_retired_key_beyond_deprecation_window_hard_fails():
    retired_at = NOW - KEY_DEPRECATION_WINDOW - timedelta(days=1)
    kr = {
        DEFAULT_ISSUER_KEY_ID: KeyRecord(
            key_id=DEFAULT_ISSUER_KEY_ID, retired_at=retired_at
        )
    }
    with pytest.raises(VerifyReject, match="key_retired_beyond_window"):
        offline_verify(_cred(), _fresh_list(), keyring=kr, now=NOW)


def test_g_b_audit_chain_join_reconstructs_credential_to_issue_audit(
    slice4_client, monkeypatch
):
    """Positive-space: credential_ledger.audit_query_id → one ncvet_issuer audit row."""
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    issued = slice4_client.post(
        "/v1/credentials/issue",
        headers=_dev_auth(ISSUER),
        json={"session_id": SESSION_ID},
    )
    assert issued.status_code == 200
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.execute(select(CredentialLedgerRow)).scalar_one()
        audit_qid = row.audit_query_id
    issuer_reads = [
        e
        for e in audit.events
        if e.caller_kind == "ncvet_issuer"
        and e.query_kind == "get_session_evidence"
        and e.query_id == audit_qid
    ]
    assert len(issuer_reads) == 1

    # Status fetch emits separate ncvet_verifier row (not the join target).
    slice4_client.get(
        "/v1/credentials/status_list",
        headers=_dev_auth(FETCHER),
    ).raise_for_status()
    verifier_reads = [e for e in audit.events if e.caller_kind == "ncvet_verifier"]
    assert len(verifier_reads) >= 1
    assert all(e.query_id != audit_qid for e in verifier_reads)

    # Negative-space: issuer path kinds stay issuer-only
    issuer_kinds = {e.caller_kind for e in audit.events if e.query_id == audit_qid}
    assert issuer_kinds <= {"ncvet_issuer"}
    assert "ncvet_audit" not in issuer_kinds
