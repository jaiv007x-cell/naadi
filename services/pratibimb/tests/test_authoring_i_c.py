"""I.c: ARP surface=arp cutover — 7-case acceptance matrix."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

pytestmark = pytest.mark.i_acceptance

from services.pratibimb.audit.metrics import AUDIT_SINK_FAILURE_TOTAL
from services.pratibimb.audit.sink import AuditEvent, InMemoryAuditSink
from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.ledger.db import get_engine
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_i_a import (
    RECOMPUTE,
    SESSION_ID,
    _arp_body,
    _seed_eligible,
    _seed_transcript,
)
from services.pratibimb.tests.test_authoring_phase_d_slice4 import _dev_auth, slice4_client
from shared.schemas.ledger_read import ConsentScope

VERIFY = "ncvet-arp-verifier-i-c"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK = _REPO_ROOT / "docs" / "ops" / "credentials_regrade_observability.md"
_PRATIBIMB = _REPO_ROOT / "services" / "pratibimb"
_CLOSED = frozenset({"catalog", "ncvet", "credentials", "regrade", "arp"})
NOW = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)


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
def _i_c_seed(slice4_client):
    AUDIT_SINK_FAILURE_TOTAL._values.clear()  # type: ignore[attr-defined]
    _seed_scope(RECOMPUTE, ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC)
    _seed_scope(VERIFY, ConsentScope.NCVET_VERIFY_ARP)
    _seed_transcript()
    _seed_eligible()
    yield


def _arp_surface_count() -> int:
    return sum(
        n
        for labels, n in AUDIT_SINK_FAILURE_TOTAL.collect()
        if dict(labels).get("surface") == "arp"
    )


def _regrade_surface_count() -> int:
    return sum(
        n
        for labels, n in AUDIT_SINK_FAILURE_TOTAL.collect()
        if dict(labels).get("surface") == "regrade"
    )


def test_i_c_1_mint_fail_closed_surface_arp(slice4_client):
    from services.pratibimb.app.main import app
    import services.pratibimb.arp.routes as routes
    from services.pratibimb.arp.gateway import JwtArpGateway
    from services.pratibimb.arp.service import ArpService
    from services.pratibimb.ledger_read.auth import ConsentResolver
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    before_arp = _arp_surface_count()
    before_regrade = _regrade_surface_count()

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
        assert _arp_surface_count() > before_arp
        assert _regrade_surface_count() == before_regrade
    finally:
        app.dependency_overrides.pop(routes.get_arp_gateway, None)


def test_i_c_2_verify_fail_closed_surface_arp(slice4_client):
    from services.pratibimb.app.main import app
    import services.pratibimb.arp.routes as routes
    from services.pratibimb.arp.gateway import JwtArpGateway
    from services.pratibimb.arp.service import ArpService
    from services.pratibimb.ledger_read.auth import ConsentResolver
    from services.pratibimb.ledger_read.deps import get_in_memory_consent_store

    env = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    ).json()

    class _FailSink:
        def emit(self, event):
            raise RuntimeError("sink_down")

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    before_arp = _arp_surface_count()
    before_regrade = _regrade_surface_count()

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
        assert _arp_surface_count() > before_arp
        assert _regrade_surface_count() == before_regrade
    finally:
        app.dependency_overrides.pop(routes.get_arp_gateway, None)


def test_i_c_3_arp_sites_resolve_surface_arp():
    for rel in ("arp/service.py", "arp/verify_audit.py"):
        text = (_PRATIBIMB / rel).read_text(encoding="utf-8")
        m = re.search(r'_SURFACE\s*=\s*["\']([^"\']+)["\']', text)
        assert m and m.group(1) == "arp", rel


def test_i_c_4_closed_surface_enum_exactly_five():
    from services.pratibimb.tests.test_authoring_h_c_2 import _CLOSED_SURFACES

    assert _CLOSED_SURFACES == _CLOSED
    text = _RUNBOOK.read_text(encoding="utf-8")
    for s in _CLOSED:
        assert f"`{s}`" in text or f'surface="{s}"' in text


def test_i_c_5_runbook_five_rows_and_cutover_pins():
    text = _RUNBOOK.read_text(encoding="utf-8")
    table = text.split("## Surface table")[1].split("##")[0]
    for s in ("catalog", "ncvet", "credentials", "regrade", "arp"):
        assert f"`{s}`" in table or s in table
    assert "arp-surface-cutover@" in text
    assert "sha:" in text
    assert 'surface=~"regrade|arp"' in text or "surface=~\"regrade|arp\"" in text
    assert "not rewritten" in text.lower() or "unchanged" in text.lower()
    assert "ncvet_recompute" in text and "ncvet_arp_verifier" in text
    assert "authoring-platform-oncall-lead" in text
    assert "deliberate" in text.lower() or "co-schedule" in text.lower()
    # Six cells for arp row: PromQL, threshold, alert, on-call, escalation (+ surface)
    assert 'surface="arp"' in text
    assert "503" in text
    assert "i_c_ship_checklist" in text
    assert "caller_kind" in text
    assert "ncvet_regrader" in text
    # I.c.2: dual annotation + C4 landed + clock reset
    assert "status-list-v2-only@" in text
    assert "arp-surface-cutover@" in text
    assert "cutover-only" in text.lower() or "separate deploy" in text.lower()
    assert "increase(status_list_compat_coercion_total" in text
    assert "resets" in text.lower() or "reset" in text.lower()
    assert "normalize_status_list_entries" in text or "status_list.py" in text


def test_i_c_6_regrade_h_still_surface_regrade_no_cross_contamination():
    """H regrade emitters stay on surface=regrade; ARP modules do not."""
    for rel, expected in (
        ("regrade/service.py", "regrade"),
        ("regrade/verify_audit.py", "regrade"),
        ("arp/service.py", "arp"),
        ("arp/verify_audit.py", "arp"),
    ):
        text = (_PRATIBIMB / rel).read_text(encoding="utf-8")
        m = re.search(r'_SURFACE\s*=\s*["\']([^"\']+)["\']', text)
        assert m and m.group(1) == expected, rel


def test_i_c_7_pre_cutover_arp_queryable_via_caller_kind():
    """C2 / P8 — fixture: ARP under surface=regrade distinguishable by caller_kind."""
    sink = InMemoryAuditSink()
    # Synthetic pre-cutover ARP Shape A row (would have been surface=regrade in metrics)
    sink.emit(
        AuditEvent(
            query_id="pre-cutover-arp-1",
            at_utc=NOW,
            tenant_id=TENANT,
            subject_pseudo_id="s1",
            actor_subject_id="s1",
            caller_kind="ncvet_arp_verifier",
            scope="ncvet:verify_arp",
            query_kind="verify_arp_assist",
            query_params_hash="a" * 64,
            query_params_bytes=10,
            outcome="error",
            duration_ms=0,
            error_kind="audit_unavailable",
        )
    )
    sink.emit(
        AuditEvent(
            query_id="pre-cutover-h-1",
            at_utc=NOW,
            tenant_id=TENANT,
            subject_pseudo_id="s2",
            actor_subject_id="s2",
            caller_kind="ncvet_regrader",
            scope="ncvet:regrade_session",
            query_kind="regrade_mint",
            query_params_hash="b" * 64,
            query_params_bytes=10,
            outcome="error",
            duration_ms=0,
            error_kind="audit_unavailable",
        )
    )
    arp_kinds = frozenset({"ncvet_recompute", "ncvet_arp_verifier"})
    pre_cutover_arp = [e for e in sink.events if e.caller_kind in arp_kinds]
    h_only = [e for e in sink.events if e.caller_kind == "ncvet_regrader"]
    assert len(pre_cutover_arp) == 1
    assert len(h_only) == 1
    assert pre_cutover_arp[0].query_id != h_only[0].query_id
