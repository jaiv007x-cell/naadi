"""H.c: observability — Shape A audit, surface labels, entropy, runbook greps."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

pytestmark = pytest.mark.h_acceptance

from services.pratibimb.audit.metrics import AUDIT_SINK_FAILURE_TOTAL
from services.pratibimb.audit.sink import InMemoryAuditSink
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.ledger.db import get_engine, init_ledger_schema
from services.pratibimb.ledger_read.deps import reset_auth_wiring_cache
from services.pratibimb.regrade.digest import compute_transcript_digest
from services.pratibimb.regrade.service import REGRADE_ID_ENTROPY_BYTES
from services.pratibimb.regrade.verify import offline_verify_regrade
from services.pratibimb.regrade.verify_audit import FETCH_ARTIFACT_KIND, VERIFY_ASSIST_KIND
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import _dev_auth, slice4_client
from shared.schemas.ledger_read import ConsentScope

REGRADE = "ncvet-regrader-h-c-1"
VERIFIER = "ncvet-verifier-h-c-2"
SESSION_ID = "sess-regrade-h-c-1"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK = _REPO_ROOT / "docs" / "ops" / "credentials_regrade_observability.md"
_CLOSED_SURFACES = frozenset({"catalog", "ncvet", "credentials", "regrade", "arp"})
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)

# Five closed surfaces after I.c; regrade = H only; arp = ARP mint + verify.
_EXPECTED_SINK_FAILURE_SITES = (
    "ledger_read/catalog_audit.py",
    "ledger_read/ncvet_audit.py",
    "credentials/status_service.py",
    "regrade/service.py",
    "regrade/verify_audit.py",
    "arp/service.py",
    "arp/verify_audit.py",
)


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


def _seed_transcript() -> None:
    from services.pratibimb.ledger.models import SessionTranscriptProjectionRow

    payload = {"actions": [{"id": "a1"}], "case_id": "hc", "rubric_schema_version": "rubric.v1"}
    digest, _ = compute_transcript_digest(payload)
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
                tenant_id=TENANT,
                transcript_digest=digest,
                rubric_schema_version="rubric.v1",
                payload_json=json.dumps(payload, sort_keys=True),
                projected_at=NOW,
            )
        )
        session.commit()


def _patch_audit_sink(monkeypatch, audit) -> None:
    from services.pratibimb.ledger_read import deps as ledger_deps
    import services.pratibimb.regrade.routes as regrade_routes

    ledger_deps.get_audit_sink.cache_clear()
    monkeypatch.setattr(ledger_deps, "get_audit_sink", lambda: audit)
    monkeypatch.setattr(regrade_routes, "get_audit_sink", lambda: audit)
    reset_auth_wiring_cache()


@pytest.fixture(autouse=True)
def _h_c_seed(slice4_client):
    AUDIT_SINK_FAILURE_TOTAL._values.clear()  # type: ignore[attr-defined]
    _seed_scope(REGRADE, ConsentScope.NCVET_REGRADE_SESSION)
    _seed_scope(VERIFIER, ConsentScope.NCVET_VERIFY_REGRADE)
    _seed_transcript()
    yield


def test_h_c_1_runbook_surface_table_and_roles():
    text = _RUNBOOK.read_text(encoding="utf-8")
    for surface in _CLOSED_SURFACES:
        assert surface in text
    assert "2026-09-22" in text
    assert "provisional" in text.lower()
    table = text.split("## Surface table")[1].split("##")[0]
    assert "Authoring platform on-call" in table
    assert "@alice" not in table.lower()
    # Threshold review: named role-owner + named artifact (H.c.1 countersign).
    assert "authoring-platform-oncall-lead" in text
    assert "credentials_regrade_observability.md" in text
    assert "production-signal" in text.lower()


def test_h_c_2_runbook_alert_rate_gt_zero():
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "rate > 0" in text
    assert "2m" in text
    assert "suppression" in text.lower()


def test_h_c_3_surface_labels_closed_set():
    root = _REPO_ROOT / "services" / "pratibimb"
    hits: list[str] = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r'surface\s*=\s*["\']([^"\']+)["\']', line):
                val = m.group(1)
                if val not in _CLOSED_SURFACES:
                    hits.append(f"{path}:{i}:{val}")
            if 'surface=_SURFACE' in line and "regrade" in path.as_posix():
                pass  # constant resolves to regrade
    assert hits == [], hits


def test_h_c_4_named_sink_failure_call_sites():
    """
    Expected AUDIT_SINK_FAILURE_TOTAL.labels(...).inc() production sites (H.c pin 3):

    - ledger_read/catalog_audit.py       (surface=catalog)
    - ledger_read/ncvet_audit.py         (surface=ncvet)
    - credentials/status_service.py      (surface=credentials)
    - regrade/service.py                 (surface=regrade, mint)
    - regrade/verify_audit.py            (surface=regrade, Shape A callback)
    - arp/service.py                     (surface=arp, I mint)
    - arp/verify_audit.py                (surface=arp, I Shape A)
    """
    root = _REPO_ROOT / "services" / "pratibimb"
    for rel in _EXPECTED_SINK_FAILURE_SITES:
        path = root.joinpath(*rel.split("/"))
        text = path.read_text(encoding="utf-8")
        assert "AUDIT_SINK_FAILURE_TOTAL" in text, rel
        assert ".inc()" in text, rel


def test_h_c_5_shape_a_verify_emits_ncvet_verifier(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    minted = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert minted.status_code == 200
    env = minted.json()
    offline_verify_regrade(env)
    before = len([e for e in audit.events if e.caller_kind == "ncvet_verifier"])
    assist = slice4_client.post(
        "/v1/regrade/verify",
        headers=_dev_auth(VERIFIER),
        json={"envelope": env},
    )
    assert assist.status_code == 200
    verifier_events = [e for e in audit.events if e.caller_kind == "ncvet_verifier"]
    assert len(verifier_events) == before + 1
    assert verifier_events[-1].query_kind == VERIFY_ASSIST_KIND


def test_h_c_6_shape_a_fetch_emits_and_sink_fail_503(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    minted = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    rid = minted.json()["regrade_id"]
    ok = slice4_client.get(
        f"/v1/regrade/artifacts/{rid}",
        headers=_dev_auth(VERIFIER),
    )
    assert ok.status_code == 200
    assert any(
        e.query_kind == FETCH_ARTIFACT_KIND and e.caller_kind == "ncvet_verifier"
        for e in audit.events
    )

    class _Fail:
        def emit(self, event) -> None:
            raise RuntimeError("sink down")

    from services.pratibimb.app.main import app
    import services.pratibimb.regrade.routes as routes
    from services.pratibimb.ledger_read.auth import ConsentResolver
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store
    from services.pratibimb.regrade.gateway import JwtRegradeGateway
    from services.pratibimb.regrade.service import RegradeService

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)

    def _gw():
        return JwtRegradeGateway(
            RegradeService(SessionLocal),
            ConsentResolver(get_in_memory_consent_store()),
            audit_sink=_Fail(),
        )

    app.dependency_overrides[routes.get_regrade_gateway] = _gw
    try:
        resp = slice4_client.get(
            f"/v1/regrade/artifacts/{rid}",
            headers=_dev_auth(VERIFIER),
        )
        assert resp.status_code == 503
        labeled = [
            (labels, n)
            for labels, n in AUDIT_SINK_FAILURE_TOTAL.collect()
            if dict(labels).get("surface") == "regrade"
        ]
        assert labeled and labeled[0][1] >= 1
    finally:
        app.dependency_overrides.pop(routes.get_regrade_gateway, None)


def test_h_c_7_regrade_id_entropy_floor(slice4_client, monkeypatch):
    from services.pratibimb.credentials.service import EVIDENCE_REF_ENTROPY_BYTES

    assert REGRADE_ID_ENTROPY_BYTES == 32
    assert REGRADE_ID_ENTROPY_BYTES == EVIDENCE_REF_ENTROPY_BYTES
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert resp.status_code == 200
    assert len(resp.json()["regrade_id"]) >= 43


def test_h_c_8_017_checklist_linked():
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "017_regrade_artifact_unique_checklist" in text
    assert "grader_version" in text.lower()
    assert "rollback" in text.lower()
    checklist = (
        _REPO_ROOT / "docs" / "migrations" / "017_regrade_artifact_unique_checklist.md"
    ).read_text(encoding="utf-8")
    assert "Rollback order" in checklist


def test_h_c_9_mint_vs_verify_caller_kinds(slice4_client, monkeypatch):
    audit = InMemoryAuditSink()
    _patch_audit_sink(monkeypatch, audit)
    minted = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
    )
    assert minted.status_code == 200
    slice4_client.post(
        "/v1/regrade/verify",
        headers=_dev_auth(VERIFIER),
        json={"envelope": minted.json()},
    ).raise_for_status()
    kinds = {e.caller_kind for e in audit.events}
    assert kinds <= {"ncvet_verifier"}
    assert "ncvet_verifier" in kinds
