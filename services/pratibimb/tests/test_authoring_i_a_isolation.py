"""I.a isolation — I-6a…f scope zero-implication (six distinct tests; I-6f I.b target pinned)."""
from __future__ import annotations

import asyncio

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

pytestmark = pytest.mark.i_acceptance

from services.pratibimb.authoring.deps import seed_authoring_scope
from services.pratibimb.tests.test_auth_jwt import TENANT
from services.pratibimb.tests.test_authoring_phase_d_slice4 import _dev_auth, slice4_client
from services.pratibimb.tests.test_authoring_i_a import (
    ALT_RUBRIC,
    ALT_VER,
    SESSION_ID,
    _arp_body,
    _seed_eligible,
    _seed_transcript,
)
from shared.schemas.ledger_read import ConsentScope

RECOMPUTE = "iso-recompute"
VERIFY_ARP = "iso-verify-arp"
REGRADE = "iso-regrade"
VERIFY_REGRADE = "iso-verify-regrade"


def _seed(subject: str, scope: ConsentScope) -> None:
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
def _iso_seed(slice4_client):
    _seed(RECOMPUTE, ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC)
    _seed(VERIFY_ARP, ConsentScope.NCVET_VERIFY_ARP)
    _seed(REGRADE, ConsentScope.NCVET_REGRADE_SESSION)
    _seed(VERIFY_REGRADE, ConsentScope.NCVET_VERIFY_REGRADE)
    _seed_transcript()
    _seed_eligible()
    yield


def test_i_6a_recompute_alone_no_h_regrade(slice4_client):
    resp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
    )
    assert resp.status_code == 403


def test_i_6b_verify_arp_alone_no_h_verify(slice4_client):
    # H verify needs an envelope; scope deny should fire first.
    resp = slice4_client.post(
        "/v1/regrade/verify",
        headers=_dev_auth(VERIFY_ARP),
        json={"envelope": {"arp_id": "x"}},
    )
    assert resp.status_code == 403


def test_i_6c_regrade_alone_no_arp_recompute(slice4_client):
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(REGRADE),
        json=_arp_body(),
    )
    assert resp.status_code == 403


def test_i_6d_verify_regrade_alone_no_arp_verify_route(slice4_client):
    # ARP verify not in I.a — route absent → 404/405; holding verify_regrade must not mint ARP.
    resp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(VERIFY_REGRADE),
        json=_arp_body(),
    )
    assert resp.status_code == 403


def test_i_6f_recompute_alone_no_arp_verify(slice4_client):
    """I-6f: recompute scope ↛ verify_arp assist — 403 and zero verifier emit."""
    from services.pratibimb.ledger_read.deps import get_audit_sink

    sink = get_audit_sink()
    before = len([e for e in sink.events if e.caller_kind == "ncvet_arp_verifier"])
    resp = slice4_client.post(
        "/v1/arp/verify",
        headers=_dev_auth(RECOMPUTE),
        json={"envelope": {"arp_id": "placeholder"}},
    )
    assert resp.status_code == 403
    after = len([e for e in sink.events if e.caller_kind == "ncvet_arp_verifier"])
    assert after == before


def test_i_6e_four_scopes_distinct_trails(slice4_client):
    # Holding all four: mint ARP with recompute; mint H with regrade — distinct tables/kinds.
    for subject, scope in (
        (RECOMPUTE, ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC),
        (REGRADE, ConsentScope.NCVET_REGRADE_SESSION),
        (VERIFY_ARP, ConsentScope.NCVET_VERIFY_ARP),
        (VERIFY_REGRADE, ConsentScope.NCVET_VERIFY_REGRADE),
    ):
        _seed(subject, scope)
    # Dual-scope subject
    dual = "iso-dual"
    _seed(dual, ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC)
    _seed(dual, ConsentScope.NCVET_REGRADE_SESSION)
    arp = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(dual),
        json=_arp_body(),
    )
    assert arp.status_code == 200, arp.text
    rcp = slice4_client.post(
        f"/v1/regrade/session/{SESSION_ID}",
        headers=_dev_auth(dual),
    )
    assert rcp.status_code == 200, rcp.text
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker
    from services.pratibimb.ledger.db import get_engine
    from services.pratibimb.ledger.models import ArpAuditRow, RegradeAuditRow

    SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    with SessionLocal() as session:
        arp_kinds = {r.caller_kind for r in session.execute(select(ArpAuditRow)).scalars()}
        regrade_kinds = {
            r.caller_kind for r in session.execute(select(RegradeAuditRow)).scalars()
        }
    assert arp_kinds == {"ncvet_recompute"}
    assert regrade_kinds == {"ncvet_regrader"}
    assert arp_kinds.isdisjoint(regrade_kinds)
