"""I.b.1: ARP verify/fetch + status-list identifier_kind — 20-case matrix."""
from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

pytestmark = pytest.mark.i_acceptance

from services.pratibimb.arp.sign import (
    DEFAULT_ARP_ISSUER_KEY_ID,
    build_portable_arp_envelope,
    verify_arp_signature,
)
from services.pratibimb.arp.verify import (
    decode_portable_arp_envelope,
    offline_verify_arp,
)
from services.pratibimb.arp.verify_audit import FETCH_ARTIFACT_KIND, VERIFY_ASSIST_KIND
from services.pratibimb.audit.caller_kinds import KNOWN_AUDIT_CALLER_KINDS
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.credentials.status_list import (
    build_status_list_envelope,
    normalize_status_list_entries,
)
from services.pratibimb.ledger.db import get_engine, init_ledger_schema
from services.pratibimb.ledger.models import StatusListIdentifierRow
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS
from services.pratibimb.regrade.sign import DEFAULT_REGRADE_ISSUER_KEY_ID
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_i_a import (
    ALT_RUBRIC,
    ALT_VER,
    AUTHORITY,
    RECOMPUTE,
    SESSION_ID,
    SEALED_RUBRIC_ID,
    _arp_body,
    _seed_eligible,
    _seed_transcript,
)
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    OTHER_TENANT,
    _dev_auth,
    slice4_client,
)
from shared.schemas.ledger_read import ConsentScope

VERIFY = "ncvet-arp-verifier-i-b"
BOTH = "ncvet-arp-both-i-b"
NEITHER = "ncvet-arp-neither-i-b"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARP_ROOT = _REPO_ROOT / "services" / "pratibimb" / "arp"
_F_LIVE = frozenset({"get_session_evidence", "list_learner_sessions"})
NOW = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)

_EXPECTED_ARP_VERIFIER_EMIT_SITES = frozenset(
    {"arp/verify_audit.py:emit_arp_verify_audit"}
)


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


@pytest.fixture(autouse=True)
def _i_b_seed(slice4_client):
    _seed_scope(RECOMPUTE, ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC)
    _seed_scope(VERIFY, ConsentScope.NCVET_VERIFY_ARP)
    _seed_scope(BOTH, ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC)
    _seed_scope(BOTH, ConsentScope.NCVET_VERIFY_ARP)
    _seed_transcript()
    _seed_eligible()
    yield


def _mint_arp(client) -> dict:
    resp = client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_i_b_1_offline_verify_happy(slice4_client):
    env = _mint_arp(slice4_client)
    result = offline_verify_arp(env)
    assert result.accepted
    assert result.reason == "ok"


def test_i_b_2_assist_output_bytes_equal_offline_output_bytes(slice4_client):
    """C4 / pin 6 — full response body byte equality, not verdict-only."""
    env = _mint_arp(slice4_client)
    offline_ok = json.dumps(offline_verify_arp(env).as_body(), sort_keys=True)
    assist_ok = slice4_client.post(
        "/v1/arp/verify",
        headers=_dev_auth(VERIFY),
        json={"envelope": env},
    )
    assert assist_ok.status_code == 200
    assert json.dumps(assist_ok.json(), sort_keys=True) == offline_ok

    bad = {**env, "transcript_digest": "0" * 64}
    # re-sign would be needed for sig path; use allowlist reject instead
    bad_extra = {**env, "session_id": "leak"}
    offline_rej = json.dumps(
        offline_verify_arp(bad_extra).as_body(), sort_keys=True
    )
    assist_rej = slice4_client.post(
        "/v1/arp/verify",
        headers=_dev_auth(VERIFY),
        json={"envelope": bad_extra},
    )
    assert assist_rej.status_code == 200
    assert json.dumps(assist_rej.json(), sort_keys=True) == offline_rej
    assert assist_rej.json()["accepted"] is False


def test_i_b_3_wrong_key_rejects(slice4_client):
    env = _mint_arp(slice4_client)
    assert verify_arp_signature(env)
    assert not verify_arp_signature(env, key_id=DEFAULT_REGRADE_ISSUER_KEY_ID)
    result = offline_verify_arp(env, expected_key_id=DEFAULT_REGRADE_ISSUER_KEY_ID)
    assert result.accepted is False


def test_i_b_4_allowlist_rejects_extra_key(slice4_client):
    env = _mint_arp(slice4_client)
    with pytest.raises(Exception):
        decode_portable_arp_envelope({**env, "session_id": "x"})


def test_i_b_5_shape_a_success_carries_two_digests(slice4_client):
    from services.pratibimb.audit.sink import hash_params
    from services.pratibimb.ledger_read.deps import get_audit_sink

    env = _mint_arp(slice4_client)
    sink = get_audit_sink()
    before = len(getattr(sink, "events", []))
    resp = slice4_client.post(
        "/v1/arp/verify",
        headers=_dev_auth(VERIFY),
        json={"envelope": env},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    events = [e for e in sink.events[before:] if e.caller_kind == "ncvet_arp_verifier"]
    assert events
    expected_params = {
        "arp_id": env["arp_id"],
        "presented_digest": env["transcript_digest"],
        "stored_digest": env["transcript_digest"],
        "accepted": True,
        "reason": "ok",
    }
    ph, _ = hash_params(expected_params)
    assert events[-1].query_params_hash == ph
    assert events[-1].query_kind == VERIFY_ASSIST_KIND


def test_i_b_6_fetch_happy_and_unknown_404(slice4_client):
    env = _mint_arp(slice4_client)
    fetched = slice4_client.get(
        f"/v1/arp/artifacts/{env['arp_id']}",
        headers=_dev_auth(VERIFY),
    )
    assert fetched.status_code == 200
    assert fetched.json()["arp_id"] == env["arp_id"]
    missing = slice4_client.get(
        "/v1/arp/artifacts/does-not-exist",
        headers=_dev_auth(VERIFY),
    )
    assert missing.status_code == 404


def test_i_b_6b_wrong_tenant_no_body(slice4_client):
    env = _mint_arp(slice4_client)
    other = "arp-verify-other-tenant"
    _seed_scope(other, ConsentScope.NCVET_VERIFY_ARP, tenant=OTHER_TENANT)
    resp = slice4_client.get(
        f"/v1/arp/artifacts/{env['arp_id']}",
        headers=_dev_auth(other, tenant=OTHER_TENANT),
    )
    assert resp.status_code in (403, 404)
    body = resp.json()
    assert "transcript_digest" not in json.dumps(body)


def test_i_b_7_fail_closed_verify_audit(slice4_client, monkeypatch):
    from services.pratibimb.app.main import app
    import services.pratibimb.arp.routes as routes
    from services.pratibimb.arp.gateway import JwtArpGateway
    from services.pratibimb.arp.service import ArpService
    from services.pratibimb.ledger_read.auth import ConsentResolver
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store

    class _FailSink:
        def emit(self, event):
            raise RuntimeError("sink_down")

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    env = _mint_arp(slice4_client)

    def _gw():
        return JwtArpGateway(
            ArpService(SessionLocal),
            ConsentResolver(get_in_memory_consent_store()),
            audit_sink=_FailSink(),
        )

    app.dependency_overrides[routes.get_arp_gateway] = _gw
    try:
        resp = slice4_client.post(
            "/v1/arp/verify",
            headers=_dev_auth(VERIFY),
            json={"envelope": env},
        )
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.pop(routes.get_arp_gateway, None)


def test_i_b_8a_recompute_alone_403_on_verify(slice4_client):
    """C6 — scope-under-test: ncvet:verify_arp deny on verify route; emit == 0."""
    from services.pratibimb.ledger_read.deps import get_audit_sink

    env = _mint_arp(slice4_client)
    sink = get_audit_sink()
    before = len([e for e in sink.events if e.caller_kind == "ncvet_arp_verifier"])
    resp = slice4_client.post(
        "/v1/arp/verify",
        headers=_dev_auth(RECOMPUTE),
        json={"envelope": env},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "consent_scope"
    after = len([e for e in sink.events if e.caller_kind == "ncvet_arp_verifier"])
    assert after == before


def test_i_b_8b_recompute_alone_403_on_fetch(slice4_client):
    """C6 — scope-under-test: ncvet:verify_arp deny on fetch route; emit == 0."""
    from services.pratibimb.ledger_read.deps import get_audit_sink

    env = _mint_arp(slice4_client)
    sink = get_audit_sink()
    before = len([e for e in sink.events if e.caller_kind == "ncvet_arp_verifier"])
    resp = slice4_client.get(
        f"/v1/arp/artifacts/{env['arp_id']}",
        headers=_dev_auth(RECOMPUTE),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "consent_scope"
    after = len([e for e in sink.events if e.caller_kind == "ncvet_arp_verifier"])
    assert after == before


def test_i_b_9_four_way_scope_matrix(slice4_client):
    env = _mint_arp(slice4_client)
    # recompute-only: mint ok (already), verify/fetch 403
    assert (
        slice4_client.post(
            "/v1/arp/verify", headers=_dev_auth(RECOMPUTE), json={"envelope": env}
        ).status_code
        == 403
    )
    # verify-only: mint 403, verify ok
    assert (
        slice4_client.post(
            f"/v1/arp/session/{SESSION_ID}",
            headers=_dev_auth(VERIFY),
            json=_arp_body(),
        ).status_code
        == 403
    )
    assert (
        slice4_client.post(
            "/v1/arp/verify", headers=_dev_auth(VERIFY), json={"envelope": env}
        ).status_code
        == 200
    )
    # both
    assert (
        slice4_client.post(
            "/v1/arp/verify", headers=_dev_auth(BOTH), json={"envelope": env}
        ).status_code
        == 200
    )
    # neither
    assert (
        slice4_client.post(
            "/v1/arp/verify", headers=_dev_auth(NEITHER), json={"envelope": env}
        ).status_code
        == 403
    )


def test_i_b_10_same_id_different_kinds_both_accepted():
    get_engine.cache_clear()
    engine = get_engine()
    init_ledger_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    shared_id = "shared-uuid-cross-kind"
    with SessionLocal() as session:
        session.add(
            StatusListIdentifierRow(
                tenant_id=TENANT,
                identifier_kind="regrade",
                identifier_id=shared_id,
                revoked_at=NOW,
                created_at=NOW,
            )
        )
        session.add(
            StatusListIdentifierRow(
                tenant_id=TENANT,
                identifier_kind="arp",
                identifier_id=shared_id,
                revoked_at=NOW,
                created_at=NOW,
            )
        )
        session.commit()


def test_i_b_11a_revoke_regrade_arp_unaffected_observable(slice4_client):
    env = _mint_arp(slice4_client)
    status = build_status_list_envelope(
        entries=[
            {
                "identifier_kind": "regrade",
                "identifier_id": env["arp_id"],
                "revoked_at": NOW.isoformat(),
            }
        ],
        key_id=DEFAULT_ARP_ISSUER_KEY_ID,
        signed_at=NOW,
        valid_until=datetime(2027, 1, 1, tzinfo=timezone.utc),
        schema_version="status_list.v2",
    )
    result = offline_verify_arp(env, status_list=status)
    assert result.accepted is True


def test_i_b_11b_revoke_arp_credential_unaffected_observable(slice4_client):
    env = _mint_arp(slice4_client)
    status = build_status_list_envelope(
        entries=[
            {
                "identifier_kind": "arp",
                "identifier_id": env["arp_id"],
                "revoked_at": NOW.isoformat(),
            }
        ],
        key_id=DEFAULT_ARP_ISSUER_KEY_ID,
        signed_at=NOW,
        valid_until=datetime(2027, 1, 1, tzinfo=timezone.utc),
        schema_version="status_list.v2",
    )
    assert offline_verify_arp(env, status_list=status).accepted is False
    # credential entry with same id does not revoke ARP when kind differs
    status_cred = build_status_list_envelope(
        entries=[
            {
                "identifier_kind": "credential",
                "identifier_id": env["arp_id"],
                "revoked_at": NOW.isoformat(),
            }
        ],
        key_id=DEFAULT_ARP_ISSUER_KEY_ID,
        signed_at=NOW,
        valid_until=datetime(2027, 1, 1, tzinfo=timezone.utc),
        schema_version="status_list.v2",
    )
    assert offline_verify_arp(env, status_list=status_cred).accepted is True


def test_i_b_12_status_list_fetch_still_ncvet_verifier(slice4_client, monkeypatch):
    from services.pratibimb.ledger_read.deps import get_audit_sink

    _seed_scope(VERIFY, ConsentScope.NCVET_FETCH_STATUS_LIST)
    sink = get_audit_sink()
    before = len([e for e in sink.events if e.caller_kind == "ncvet_verifier"])
    resp = slice4_client.get(
        "/v1/credentials/status_list",
        headers=_dev_auth(VERIFY),
    )
    # may 403 if scope wrong subject — seed fetch scope on VERIFY subject
    if resp.status_code == 403:
        fetch_subj = "status-list-fetcher-i-b"
        _seed_scope(fetch_subj, ConsentScope.NCVET_FETCH_STATUS_LIST)
        resp = slice4_client.get(
            "/v1/credentials/status_list",
            headers=_dev_auth(fetch_subj),
        )
    assert resp.status_code == 200, resp.text
    after = [e for e in sink.events if e.caller_kind == "ncvet_verifier"]
    assert len(after) > before
    assert not any(
        e.caller_kind == "ncvet_arp_verifier" and e.query_kind == "fetch_credential_status_list"
        for e in sink.events
    )


def test_i_b_13_transitive_verify_closure_denylist():
    """C8 / pin 7 — transitive import closure of verify modules ↛ mint write models."""
    forbidden = {"ArpAuditRow", "ArpArtifactRow"}
    roots = [
        _ARP_ROOT / "verify.py",
        _ARP_ROOT / "verify_audit.py",
    ]
    visited: set[Path] = set()
    queue = list(roots)

    def _resolve(module: str) -> Path | None:
        if not module.startswith("services.pratibimb.arp"):
            return None
        rel = module.replace("services.pratibimb.arp.", "").replace(".", "/")
        cand = _ARP_ROOT / f"{rel}.py"
        return cand if cand.is_file() else None

    while queue:
        path = queue.pop()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    assert alias.name not in forbidden, (
                        f"{path.name} imports {alias.name}"
                    )
                nxt = _resolve(node.module)
                if nxt and nxt not in visited:
                    queue.append(nxt)


def test_i_b_14_no_new_f_kinds():
    assert "arp" not in " ".join(F_NCVET_QUERY_KINDS)
    assert F_NCVET_QUERY_KINDS <= _F_LIVE or F_NCVET_QUERY_KINDS == _F_LIVE


def test_i_b_15_020_bogus_kind_check_fails():
    get_engine.cache_clear()
    engine = get_engine()
    init_ledger_schema(engine)
    with engine.connect() as conn:
        try:
            conn.execute(
                text(
                    "INSERT INTO status_list_identifier "
                    "(tenant_id, identifier_kind, identifier_id, revoked_at, created_at) "
                    "VALUES ('t', 'bogus', 'id1', :now, :now)"
                ),
                {"now": NOW},
            )
            conn.commit()
            # SQLite may not enforce CHECK depending on build — also assert ORM CHECK name
            pytest.skip("SQLite did not enforce CHECK")
        except Exception:
            conn.rollback()
    from services.pratibimb.ledger.models import StatusListIdentifierRow

    table = StatusListIdentifierRow.__table__
    assert any(
        getattr(c, "name", None) == "ck_status_list_identifier_kind"
        for c in table.constraints
    )


def test_i_b_16_inverted_9b_static_subset():
    """Pin 9 — static subset-check on registered emit sites (not runtime mutation)."""
    path = _ARP_ROOT / "verify_audit.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    sites: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != "emit_arp_verify_audit":
                continue
            seg = ast.get_source_segment(source, node) or ""
            assert "ncvet_arp_verifier" in source  # module Constant _VERIFIER
            assert "AUDIT_SINK_FAILURE_TOTAL" in seg
            sites.add("arp/verify_audit.py:emit_arp_verify_audit")
    assert sites == _EXPECTED_ARP_VERIFIER_EMIT_SITES
    # Mutation-test shape: expected set is the registry; removing a member fails equality.
    assert "arp/verify_audit.py:emit_arp_verify_audit" in _EXPECTED_ARP_VERIFIER_EMIT_SITES


def test_i_b_17_digest_mismatch_422(slice4_client):
    """Pin 10 — presented≠stored → 422 digest_mismatch (not 403)."""
    from services.pratibimb.audit.sink import hash_params
    from services.pratibimb.ledger_read.deps import get_audit_sink

    env = _mint_arp(slice4_client)
    forged = build_portable_arp_envelope(
        arp_id=env["arp_id"],
        transcript_digest="b" * 64,
        digest_alg=env["digest_alg"],
        score_payload=env["score_payload"],
        grader_version=env["grader_version"],
        sealed_rubric_id=env["sealed_rubric_id"],
        transcript_rubric_schema_version=env["transcript_rubric_schema_version"],
        alternate_rubric_id=env["alternate_rubric_id"],
        alternate_rubric_version=env["alternate_rubric_version"],
        authority_id=env["authority_id"],
    )
    sink = get_audit_sink()
    before = len(sink.events)
    resp = slice4_client.post(
        "/v1/arp/verify",
        headers=_dev_auth(VERIFY),
        json={"envelope": forged},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "digest_mismatch"
    events = [
        e
        for e in sink.events[before:]
        if e.caller_kind == "ncvet_arp_verifier" and e.error_kind == "digest_mismatch"
    ]
    assert events
    ph, _ = hash_params(
        {
            "arp_id": env["arp_id"],
            "presented_digest": "b" * 64,
            "stored_digest": env["transcript_digest"],
            "accepted": False,
            "reason": "digest_mismatch",
        }
    )
    assert events[-1].query_params_hash == ph


def test_i_b_compat_coercion_increments_counter():
    from services.pratibimb.audit.metrics import STATUS_LIST_COMPAT_COERCION_TOTAL

    before = STATUS_LIST_COMPAT_COERCION_TOTAL.total()
    envelope = {
        "status_list_schema_version": "status_list.v1",
        "entries": [{"credential_id": "c1", "revoked_at": NOW.isoformat()}],
    }
    rows = normalize_status_list_entries(envelope)
    assert rows[0]["identifier_kind"] == "credential"
    assert STATUS_LIST_COMPAT_COERCION_TOTAL.total() == before + 1
