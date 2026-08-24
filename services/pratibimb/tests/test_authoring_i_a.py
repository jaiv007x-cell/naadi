"""I.a: ARP mint E2E — acceptance matrix + coexistence (I-I-*)."""
from __future__ import annotations

import ast
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

pytestmark = pytest.mark.i_acceptance

from services.pratibimb.arp.sealed_grade import grade_call_count, reset_grade_call_count
from services.pratibimb.arp.service import UpdateCounter
from services.pratibimb.arp.sign import (
    DEFAULT_ARP_ISSUER_KEY_ID,
    verify_arp_signature,
)
from services.pratibimb.audit.caller_kinds import KNOWN_AUDIT_CALLER_KINDS
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.ledger.db import get_engine, init_ledger_schema
from services.pratibimb.ledger.models import (
    ArpArtifactRow,
    ArpAuditRow,
    ArpEligibleRubricRow,
    RegradeArtifactRow,
    RegradeAuditRow,
    SessionTranscriptProjectionRow,
)
from services.pratibimb.ledger_read.query_kinds import F_NCVET_QUERY_KINDS
from services.pratibimb.regrade.digest import compute_transcript_digest
from services.pratibimb.regrade.sign import DEFAULT_REGRADE_ISSUER_KEY_ID
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import (
    OTHER_TENANT,
    _dev_auth,
    slice4_client,
)
from shared.schemas.ledger_read import ConsentScope

RECOMPUTE = "ncvet-recompute-i-a-1"
REGRADE = "ncvet-regrader-i-a-2"
NO_SCOPE = "no-arp-scope-i-a-3"
SESSION_ID = "sess-arp-i-a-1"
ALT_RUBRIC = "rubric.alt.v1"
ALT_VER = "1.0.0"
AUTHORITY = "ncvet-authority-alt-1"
SEALED_RUBRIC_ID = "rubric.sealed.v1"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARP_ROOT = _REPO_ROOT / "services" / "pratibimb" / "arp"
NOW = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
_F_LIVE = frozenset({"get_session_evidence", "list_learner_sessions"})


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


def _seed_transcript(*, tamper_digest: bool = False, tenant_id: str = TENANT) -> None:
    payload = {
        "actions": [{"id": "a1"}],
        "case_id": "ia",
        "rubric_id": SEALED_RUBRIC_ID,
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
        session.add(
            SessionTranscriptProjectionRow(
                session_id=SESSION_ID,
                tenant_id=tenant_id,
                transcript_digest=stored,
                rubric_schema_version="rubric.v1",
                payload_json=json.dumps(payload, sort_keys=True),
                projected_at=NOW,
            )
        )
        session.commit()


def _seed_eligible(
    *,
    published: bool = True,
    eligible: bool = True,
    rubric_id: str = ALT_RUBRIC,
    version: str = ALT_VER,
) -> None:
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.get(ArpEligibleRubricRow, (TENANT, rubric_id, version))
        if row:
            session.delete(row)
            session.flush()
        session.add(
            ArpEligibleRubricRow(
                tenant_id=TENANT,
                alternate_rubric_id=rubric_id,
                alternate_rubric_version=version,
                authority_id=AUTHORITY,
                published=published,
                recompute_eligible=eligible,
            )
        )
        session.commit()


def _unpublish_eligible() -> None:
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.get(ArpEligibleRubricRow, (TENANT, ALT_RUBRIC, ALT_VER))
        if row:
            row.published = False
            row.recompute_eligible = False
            session.commit()


def _arp_body(**extra) -> dict:
    body = {"alternate_rubric_id": ALT_RUBRIC, "alternate_rubric_version": ALT_VER}
    body.update(extra)
    return body


@pytest.fixture(autouse=True)
def _i_a_seed(slice4_client):
    reset_grade_call_count()
    _seed_scope(RECOMPUTE, ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC)
    _seed_scope(REGRADE, ConsentScope.NCVET_REGRADE_SESSION)
    _seed_transcript()
    _seed_eligible()
    yield


def test_i_a_1_happy_arp(slice4_client):
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    )
    assert resp.status_code == 200, resp.text
    env = resp.json()
    assert env["arp_id"]
    assert "session_id" not in env
    assert env["score_payload"]["grade_total"] is not None
    assert env["alternate_rubric_id"] == ALT_RUBRIC
    assert env["authority_id"] == AUTHORITY
    assert env["arp_issuer_key_id"] == DEFAULT_ARP_ISSUER_KEY_ID
    assert verify_arp_signature(env)


def test_i_a_2_portable_excludes_session_id(slice4_client):
    env = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    ).json()
    blob = json.dumps(env)
    assert '"session_id"' not in blob
    assert "learner_pseudo" not in blob


def test_i_a_3_audit_three_forensics_fields(slice4_client):
    slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    ).raise_for_status()
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.execute(select(ArpAuditRow)).scalars().one()
        assert row.alternate_rubric_id == ALT_RUBRIC
        assert row.authority_id == AUTHORITY
        assert row.transcript_rubric_schema_version == "rubric.v1"
        assert row.caller_kind == "ncvet_recompute"


def test_i_a_4_digest_mismatch_no_grader(slice4_client):
    _seed_transcript(tamper_digest=True)
    reset_grade_call_count()
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_kind"] == "transcript_digest_mismatch"
    assert grade_call_count() == 0
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        assert session.execute(select(func.count()).select_from(ArpArtifactRow)).scalar() == 0
        audit = session.execute(select(ArpAuditRow)).scalars().one()
        assert audit.grader_outcome == "not_invoked"


def test_i_a_5_scope_deny(slice4_client):
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(NO_SCOPE),
        json=_arp_body(),
    )
    assert resp.status_code == 403


def test_i_a_6_regrade_scope_does_not_imply_arp(slice4_client):
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
        json=_arp_body(),
    )
    assert resp.status_code == 403


def test_i_a_7_tenant_deny(slice4_client):
    _seed_transcript(tenant_id=OTHER_TENANT)
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    )
    assert resp.status_code in (403, 404)


def test_i_a_8_fail_closed_audit_sink(slice4_client, monkeypatch):
    from services.pratibimb.app.main import app
    import services.pratibimb.arp.routes as routes
    from services.pratibimb.arp.gateway import JwtArpGateway
    from services.pratibimb.arp.service import ArpService
    from services.pratibimb.ledger_read.auth import ConsentResolver
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)

    def _gw():
        return JwtArpGateway(
            ArpService(SessionLocal, fail_audit=True),
            ConsentResolver(get_in_memory_consent_store()),
        )

    app.dependency_overrides[routes.get_arp_gateway] = _gw
    try:
        resp = slice4_client.post(
            f"/v1/arp/session/{SESSION_ID}",
            headers=_dev_auth(RECOMPUTE),
            json=_arp_body(),
        )
        assert resp.status_code == 503
        with SessionLocal() as session:
            assert (
                session.execute(select(func.count()).select_from(ArpArtifactRow)).scalar()
                == 0
            )
    finally:
        app.dependency_overrides.pop(routes.get_arp_gateway, None)


def test_i_a_9_caller_kind_and_019_belt():
    assert "ncvet_recompute" in KNOWN_AUDIT_CALLER_KINDS
    assert "ncvet_arp_verifier" in KNOWN_AUDIT_CALLER_KINDS
    sql = (
        _REPO_ROOT
        / "services"
        / "pratibimb"
        / "ledger"
        / "migrations"
        / "019_arp_artifact.sql"
    ).read_text(encoding="utf-8")
    assert "ncvet_recompute" in sql
    assert "ncvet_arp_verifier" in sql
    assert "ux_arp_artifacts_tenant_session_digest_rubric" in sql


def test_i_a_9b_ncvet_arp_verifier_emit_sites_allowlist():
    """I.b.1 inverted 9b: emit literals only at registered sites (C2 / pin 9 static).

    Asserts *string literal* presence via ``ast.Constant`` nodes only (Amendment-2
    shape). Catches: ``caller_kind="ncvet_arp_verifier"`` / bare string constants.

    Out of scope (deliberately deferred — extend only with design review):
      (1) f-string — ``f"ncvet_{suffix}"``: AST Constant walker cannot see the
          assembled value; code-review convention forbids dynamic caller_kind.
      (2) concatenation — ``"ncvet_arp_" + "verifier"``: same; no Constant equals
          the full kind; treat as anti-pattern, not meta-test duty.
      (3) dict-lookup — ``CALLER_KINDS["arp_verifier"]``: value may live only in
          the dict; walker sees the key string, not the looked-up kind unless the
          dict value is itself a Constant at the emit site.

    Allowed emit sites after I.b.1 (static subset — case 16 also asserts):
      ``arp/verify_audit.py`` + ``caller_kinds.py`` enum membership.
    """
    kind = "ncvet_arp_verifier"
    allowed = frozenset(
        {
            "audit/caller_kinds.py",
            "arp/verify_audit.py",
        }
    )
    pratibimb = _REPO_ROOT / "services" / "pratibimb"
    found: set[str] = set()
    for path in pratibimb.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == kind:
                found.add(path.relative_to(pratibimb).as_posix())
    assert found <= allowed, f"unexpected emit sites: {found - allowed}"
    assert "arp/verify_audit.py" in found
    assert "audit/caller_kinds.py" in found


def test_i_a_10_audit_cardinality(slice4_client):
    slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    ).raise_for_status()
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        assert session.execute(select(func.count()).select_from(ArpAuditRow)).scalar() == 1


def test_i_a_11_projection_only_no_grading_import():
    for path in _ARP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "pratibimb.grading" not in node.module
                assert not node.module.endswith(".grading")


def test_i_a_12_grading_isolation_both_ways():
    arp_text = "\n".join(p.read_text(encoding="utf-8") for p in _ARP_ROOT.rglob("*.py"))
    assert "services.pratibimb.grading" not in arp_text
    grading = _REPO_ROOT / "services" / "pratibimb" / "grading"
    if grading.exists():
        for path in grading.rglob("*.py"):
            assert "services.pratibimb.arp" not in path.read_text(encoding="utf-8")


def test_i_a_13_no_new_f_ledger_kinds():
    assert F_NCVET_QUERY_KINDS == _F_LIVE or F_NCVET_QUERY_KINDS <= _F_LIVE | frozenset()
    # F live kinds frozen — ARP must not add to F_NCVET_QUERY_KINDS
    assert "arp" not in " ".join(F_NCVET_QUERY_KINDS)


def test_i_a_14_sync_mvp_no_queue():
    for path in _ARP_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "celery" not in text.lower()
        assert "create_task" not in text


def test_i_a_15_unpublished_at_grader_invoke(slice4_client):
    _seed_eligible(published=False, eligible=False)
    reset_grade_call_count()
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    )
    assert resp.status_code == 409
    assert "eligible" in resp.json()["detail"]["error_kind"] or "unpublished" in resp.json()[
        "detail"
    ]["error_kind"]
    assert grade_call_count() == 0


def test_i_a_15b_mid_grader_unpublish_race(slice4_client, monkeypatch):
    from services.pratibimb.app.main import app
    import services.pratibimb.arp.routes as routes
    from services.pratibimb.arp.gateway import JwtArpGateway
    from services.pratibimb.arp.service import ArpService
    from services.pratibimb.ledger_read.auth import ConsentResolver
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)

    def _gw():
        return JwtArpGateway(
            ArpService(SessionLocal, unpublish_hook=_unpublish_eligible),
            ConsentResolver(get_in_memory_consent_store()),
        )

    app.dependency_overrides[routes.get_arp_gateway] = _gw
    try:
        resp = slice4_client.post(
            f"/v1/arp/session/{SESSION_ID}",
            headers=_dev_auth(RECOMPUTE),
            json=_arp_body(),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_kind"] == "alternate_rubric_unpublished"
        with SessionLocal() as session:
            assert (
                session.execute(select(func.count()).select_from(ArpArtifactRow)).scalar()
                == 0
            )
    finally:
        app.dependency_overrides.pop(routes.get_arp_gateway, None)


def test_i_a_16_wrong_key_rejects():
    from services.pratibimb.arp.sign import build_portable_arp_envelope

    env = build_portable_arp_envelope(
        arp_id="x",
        transcript_digest="d",
        digest_alg="sha256-canonical-json-v0",
        score_payload={"grade_total": 0.5, "grade_passed": True},
        grader_version="arp-sealed-stub.v1",
        sealed_rubric_id=SEALED_RUBRIC_ID,
        transcript_rubric_schema_version="rubric.v1",
        alternate_rubric_id=ALT_RUBRIC,
        alternate_rubric_version=ALT_VER,
        authority_id=AUTHORITY,
    )
    assert verify_arp_signature(env)
    assert not verify_arp_signature(env, key_id=DEFAULT_REGRADE_ISSUER_KEY_ID)


def test_i_a_16b_same_rubric_as_sealed_collision(slice4_client):
    _seed_eligible(rubric_id=SEALED_RUBRIC_ID)
    reset_grade_call_count()
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json={"alternate_rubric_id": SEALED_RUBRIC_ID, "alternate_rubric_version": ALT_VER},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_kind"] == "arp_rubric_identity_collision"
    assert grade_call_count() == 0


def test_i_a_17_coexistence_rcp_byte_identical(slice4_client):
    rcp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert rcp.status_code == 200, rcp.text
    before = json.dumps(rcp.json(), sort_keys=True)
    slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    ).raise_for_status()
    rid = rcp.json()["regrade_id"]
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        row = session.get(RegradeArtifactRow, rid)
        assert row is not None
        after = json.dumps(json.loads(row.envelope_bytes), sort_keys=True)
    assert after == before


def test_i_a_18_inverse_regrade_mint_zero_arp_side_effects(slice4_client):
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        before_audit = session.execute(select(func.count()).select_from(ArpAuditRow)).scalar()
        counter = UpdateCounter("arp_artifacts")
        counter.attach(session)
        # Run regrade via HTTP (separate session) — count ARP table mutations in our session is 0;
        # assert no ARP rows created by regrade path.
    slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    ).raise_for_status()
    with SessionLocal() as session:
        after_audit = session.execute(select(func.count()).select_from(ArpAuditRow)).scalar()
        after_art = session.execute(select(func.count()).select_from(ArpArtifactRow)).scalar()
    assert after_audit == before_audit == 0
    assert after_art == 0


def test_i_a_19_structural_arp_mint_zero_regrade_updates(slice4_client):
    """Listener counts UPDATE/DELETE on regrade_artifacts during ARP mint (I-I-5 / C3).

    Production path attaches ``UpdateCounter('regrade_artifacts')`` around the
    audit+artifact write; assert fires if updates/deletes != 0. Matrix half:
    ``regrade_audit`` row-count delta == 0.
    """
    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        before = session.execute(select(func.count()).select_from(RegradeAuditRow)).scalar()

    slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    ).raise_for_status()

    with SessionLocal() as session:
        after = session.execute(select(func.count()).select_from(RegradeAuditRow)).scalar()
    assert after == before == 0

    svc_src = (_ARP_ROOT / "service.py").read_text(encoding="utf-8")
    assert "UpdateCounter" in svc_src
    assert 'UpdateCounter("regrade_artifacts")' in svc_src
