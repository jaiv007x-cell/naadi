"""H.b: regrade verify + harden — 15-case acceptance matrix."""
from __future__ import annotations

import asyncio
import json
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

from services.pratibimb.audit.metrics import AUDIT_SINK_FAILURE_TOTAL
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.ledger.db import get_engine, init_ledger_schema
from services.pratibimb.ledger.models import RegradeArtifactRow, RegradeAuditRow
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS
from services.pratibimb.regrade.digest import DIGEST_ALG_PLACEHOLDER, compute_transcript_digest
from services.pratibimb.regrade.sealed_grade import (
    GRADER_VERSION,
    grade_call_count,
    reset_grade_call_count,
)
from services.pratibimb.regrade.sign import build_portable_envelope
from services.pratibimb.regrade.verify import (
    EnvelopeReject,
    decode_portable_envelope,
    offline_verify_regrade,
)
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import _dev_auth, slice4_client
from shared.schemas.ledger_read import ConsentScope

REGRADE = "ncvet-regrader-h-b-1"
VERIFIER = "ncvet-verifier-h-b-2"
BOTH = "ncvet-both-h-b-3"
NEITHER = "ncvet-neither-h-b-4"
SESSION_ID = "sess-regrade-h-b-1"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REGRADE_ROOT = _REPO_ROOT / "services" / "pratibimb" / "regrade"
NOW = datetime(2026, 8, 23, 11, 0, tzinfo=timezone.utc)
_F_LIVE = frozenset({"get_session_evidence", "list_learner_sessions"})


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


def _seed_transcript(*, tamper_digest: bool = False) -> None:
    from services.pratibimb.ledger.models import SessionTranscriptProjectionRow

    payload = {
        "actions": [{"id": "a1"}],
        "case_id": "case-hb",
        "rubric_schema_version": "rubric.v1",
    }
    digest, _ = compute_transcript_digest(payload)
    stored = "deadbeef" * 8 if tamper_digest else digest
    get_engine.cache_clear()
    engine = get_engine()
    init_ledger_schema(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        existing = session.get(SessionTranscriptProjectionRow, SESSION_ID)
        if existing:
            session.delete(existing)
            session.flush()
        for row in session.execute(
            select(RegradeArtifactRow).where(RegradeArtifactRow.session_id == SESSION_ID)
        ).scalars():
            session.delete(row)
        for row in session.execute(
            select(RegradeAuditRow).where(RegradeAuditRow.session_id == SESSION_ID)
        ).scalars():
            session.delete(row)
        session.add(
            SessionTranscriptProjectionRow(
                session_id=SESSION_ID,
                tenant_id=TENANT,
                transcript_digest=stored,
                rubric_schema_version="rubric.v1",
                payload_json=json.dumps(payload, sort_keys=True),
                projected_at=NOW,
            )
        )
        session.commit()


@pytest.fixture(autouse=True)
def _h_b_seed(slice4_client):
    reset_grade_call_count()
    AUDIT_SINK_FAILURE_TOTAL._values.clear()  # type: ignore[attr-defined]
    _seed_scope(REGRADE, ConsentScope.NCVET_REGRADE_SESSION)
    _seed_scope(VERIFIER, ConsentScope.NCVET_VERIFY_REGRADE)
    _seed_scope(BOTH, ConsentScope.NCVET_REGRADE_SESSION)
    _seed_scope(BOTH, ConsentScope.NCVET_VERIFY_REGRADE)
    _seed_transcript()
    yield


def test_h_b_1_offline_verify_happy():
    env = build_portable_envelope(
        regrade_id="r-ok",
        transcript_digest="abc",
        grade_payload={"grade_total": 0.9, "grade_passed": True},
        grader_version=GRADER_VERSION,
        rubric_schema_version="rubric.v1",
        digest_alg=DIGEST_ALG_PLACEHOLDER,
    )
    decoded = decode_portable_envelope(env)
    result = offline_verify_regrade(decoded)
    assert result.accepted
    assert result.reason == "ok"


def test_h_b_2_assist_agrees_with_offline(slice4_client):
    minted = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert minted.status_code == 200
    env = minted.json()
    offline = offline_verify_regrade(env)
    assist = slice4_client.post(
        "/v1/regrade/verify",
        headers=_dev_auth(VERIFIER),
        json={"envelope": env},
    )
    assert assist.status_code == 200
    assert assist.json()["accepted"] is offline.accepted is True

    bad = dict(env)
    bad["grade_payload"] = {"tampered": True}
    offline_rej = offline_verify_regrade(bad)
    assist_rej = slice4_client.post(
        "/v1/regrade/verify",
        headers=_dev_auth(VERIFIER),
        json={"envelope": bad},
    )
    assert assist_rej.status_code == 200
    assert assist_rej.json()["accepted"] is offline_rej.accepted is False


def test_h_b_success_audit_carries_both_digests(slice4_client):
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 200
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        audit = session.execute(select(RegradeAuditRow)).scalar_one()
        assert audit.transcript_digest_expected
        assert audit.transcript_digest_computed
        assert audit.transcript_digest_expected == audit.transcript_digest_computed
        assert audit.digest_match is True


def test_h_b_4_mismatch_grader_call_count_zero(slice4_client):
    reset_grade_call_count()
    _seed_transcript(tamper_digest=True)
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 409
    assert grade_call_count() == 0
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        audit = session.execute(select(RegradeAuditRow)).scalar_one()
        assert audit.grader_outcome == "not_invoked"


def test_h_b_5_allowlist_rejects_extra_key():
    env = build_portable_envelope(
        regrade_id="r1",
        transcript_digest="abc",
        grade_payload={"grade_total": 0.5, "grade_passed": True},
        grader_version=GRADER_VERSION,
        rubric_schema_version="rubric.v1",
        digest_alg=DIGEST_ALG_PLACEHOLDER,
    )
    env["session_id_hint"] = "smuggle"
    with pytest.raises(EnvelopeReject, match="extra_keys"):
        decode_portable_envelope(env)


def test_h_b_6_wrong_key_reject_lib_and_assist(slice4_client):
    env = build_portable_envelope(
        regrade_id="r1",
        transcript_digest="abc",
        grade_payload={"grade_total": 0.5, "grade_passed": True},
        grader_version=GRADER_VERSION,
        rubric_schema_version="rubric.v1",
        digest_alg=DIGEST_ALG_PLACEHOLDER,
    )
    assert offline_verify_regrade(env, expected_key_id="dev-issuer-key-1").accepted is False
    bad = dict(env)
    bad["proof"] = dict(env["proof"])
    bad["proof"]["signature"] = "00" * 32
    assist = slice4_client.post(
        "/v1/regrade/verify",
        headers=_dev_auth(VERIFIER),
        json={"envelope": bad},
    )
    assert assist.status_code == 200
    assert assist.json()["accepted"] is False


def test_h_b_concurrent_regrade_second_hits_unique_constraint(slice4_client):
    first = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert first.status_code == 200
    second = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert second.status_code == 409
    assert second.json()["detail"]["error_kind"] == "regrade_duplicate"
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        arts = list(session.execute(select(RegradeArtifactRow)).scalars())
        assert len(arts) == 1
        dups = [
            a
            for a in session.execute(select(RegradeAuditRow)).scalars()
            if a.error_kind == "regrade_duplicate"
        ]
        assert len(dups) == 1
        assert dups[0].grader_invoked is True
        assert dups[0].grader_outcome == "success"
        assert dups[0].regrade_artifact_id is None


def test_h_b_8_017_sql_unique_belt():
    sql = (
        _REPO_ROOT
        / "services"
        / "pratibimb"
        / "ledger"
        / "migrations"
        / "017_regrade_artifact_unique.sql"
    ).read_text(encoding="utf-8")
    assert "ux_regrade_artifacts_tenant_session_digest" in sql


def test_h_b_9_fail_closed_increments_surface_regrade(slice4_client, monkeypatch):
    import services.pratibimb.regrade.routes as routes
    from services.pratibimb.app.main import app
    from services.pratibimb.ledger_read.auth import ConsentResolver
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store
    from services.pratibimb.regrade.gateway import JwtRegradeGateway
    from services.pratibimb.regrade.service import RegradeService

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    AUDIT_SINK_FAILURE_TOTAL._values.clear()  # type: ignore[attr-defined]

    def _failing():
        return JwtRegradeGateway(
            RegradeService(SessionLocal, fail_audit=True),
            ConsentResolver(get_in_memory_consent_store()),
        )

    app.dependency_overrides[routes.get_regrade_gateway] = _failing
    try:
        resp = slice4_client.post(
            f"/v1/regrade/session/{SESSION_ID}",
            headers=_dev_auth(REGRADE),
        )
        assert resp.status_code == 503
        with SessionLocal() as session:
            assert list(session.execute(select(RegradeArtifactRow)).scalars()) == []
        labeled = [
            (labels, n)
            for labels, n in AUDIT_SINK_FAILURE_TOTAL.collect()
            if dict(labels).get("surface") == "regrade"
        ]
        assert labeled and labeled[0][1] >= 1
    finally:
        app.dependency_overrides.pop(routes.get_regrade_gateway, None)


def test_h_b_10_grader_version_import_constant(slice4_client):
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 200
    assert resp.json()["grader_version"] == GRADER_VERSION
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        art = session.execute(select(RegradeArtifactRow)).scalar_one()
        assert art.grader_version == GRADER_VERSION


def test_h_b_11_four_way_scope_matrix(slice4_client):
    _seed_transcript()
    minted = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert minted.status_code == 200
    env = minted.json()

    # regrade-only cannot verify
    assert (
        slice4_client.post(
            "/v1/regrade/verify",
            headers=_dev_auth(REGRADE),
            json={"envelope": env},
        ).status_code
        == 403
    )
    # verify-only can verify, cannot mint
    assert (
        slice4_client.post(
            "/v1/regrade/verify",
            headers=_dev_auth(VERIFIER),
            json={"envelope": env},
        ).status_code
        == 200
    )
    assert (
        slice4_client.post(
            f"/v1/regrade/session/{SESSION_ID}",
            headers=_dev_auth(VERIFIER),
        ).status_code
        == 403
    )
    # both can verify
    assert (
        slice4_client.post(
            "/v1/regrade/verify",
            headers=_dev_auth(BOTH),
            json={"envelope": env},
        ).status_code
        == 200
    )
    # neither denied both
    assert (
        slice4_client.post(
            f"/v1/regrade/session/{SESSION_ID}",
            headers=_dev_auth(NEITHER),
        ).status_code
        == 403
    )
    assert (
        slice4_client.post(
            "/v1/regrade/verify",
            headers=_dev_auth(NEITHER),
            json={"envelope": env},
        ).status_code
        == 403
    )


def test_h_b_12_fetch_artifact_tenant_bound(slice4_client):
    minted = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert minted.status_code == 200
    rid = minted.json()["regrade_id"]
    ok = slice4_client.get(
        f"/v1/regrade/artifacts/{rid}",
        headers=_dev_auth(VERIFIER),
    )
    assert ok.status_code == 200
    assert ok.json()["regrade_id"] == rid
    assert (
        slice4_client.get(
            "/v1/regrade/artifacts/forged-id-not-real",
            headers=_dev_auth(VERIFIER),
        ).status_code
        == 404
    )


def test_h_b_13_no_new_f_ledger_kinds():
    assert F_NCVET_QUERY_KINDS == _F_LIVE


def test_h_b_14_no_update_on_audit_or_artifacts():
    for path in _REGRADE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "UPDATE regrade_audit" not in text.lower()
        assert "UPDATE regrade_artifacts" not in text.lower()


def test_h_b_15_verify_does_not_mint(slice4_client):
    assert (
        slice4_client.post(
            f"/v1/regrade/session/{SESSION_ID}",
            headers=_dev_auth(VERIFIER),
        ).status_code
        == 403
    )
    verify_src = (_REGRADE_ROOT / "verify.py").read_text(encoding="utf-8")
    assert "grade_sealed" not in verify_src
    assert "RegradeService" not in verify_src
