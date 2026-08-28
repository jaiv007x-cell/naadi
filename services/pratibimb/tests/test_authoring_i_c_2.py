"""I.c.2.1 product belt — v2-only reader retirement (P1–P3)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest_plugins = [
    "services.pratibimb.tests.test_authoring_phase_a",
    "services.pratibimb.tests.test_auth_jwt",
]

pytestmark = pytest.mark.i_c_2_acceptance

from services.pratibimb.arp.verify import (
    arp_unknown_schema_http_detail,
    offline_verify_arp,
)
from services.pratibimb.audit.metrics import STATUS_LIST_COMPAT_COERCION_TOTAL
from services.pratibimb.authoring.deps import seed_authoring_scope
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

VERIFY = "ncvet-arp-verifier-i-c-2-b4"
_ARP_ROOT = Path(__file__).resolve().parents[1] / "arp"


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
def _i_c_2_seed(slice4_client):
    _seed_scope(RECOMPUTE, ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC)
    _seed_scope(VERIFY, ConsentScope.NCVET_VERIFY_ARP)
    _seed_transcript()
    _seed_eligible()
    yield


def test_i_c_2_1_arp_v1_snapshot_fail_closed_no_coercion_counter(slice4_client):
    """B4 / P3 — ARP verify against v1 fixture: 503 unknown_status_list_schema, counter delta 0."""
    v1_list = {
        "status_list_schema_version": "status_list.v1",
        "entries": [{"credential_id": "c-pre-v2", "revoked_at": "2026-08-01T00:00:00+00:00"}],
    }
    before = STATUS_LIST_COMPAT_COERCION_TOTAL.total()
    result = offline_verify_arp({}, status_list=v1_list)
    assert result.accepted is False
    assert result.reason == "unknown_status_list_schema"
    http = arp_unknown_schema_http_detail(result)
    assert http is not None
    assert http["status_code"] == 503
    assert http["error_kind"] == "unknown_status_list_schema"
    assert STATUS_LIST_COMPAT_COERCION_TOTAL.total() == before

    env = slice4_client.post(
        f"/v1/arp/session/{SESSION_ID}",
        headers=_dev_auth(RECOMPUTE),
        json=_arp_body(),
    ).json()
    before_http = STATUS_LIST_COMPAT_COERCION_TOTAL.total()
    resp = slice4_client.post(
        "/v1/arp/verify",
        headers=_dev_auth(VERIFY),
        json={"envelope": env, "status_list": v1_list},
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["error_kind"] == "unknown_status_list_schema"
    assert STATUS_LIST_COMPAT_COERCION_TOTAL.total() == before_http

    arp_src = "\n".join(p.read_text(encoding="utf-8") for p in _ARP_ROOT.glob("*.py"))
    assert "credentials.compat" not in arp_src
    assert "compat.normalize" not in arp_src
    assert "coerce_status_list_entries_for_verify" not in arp_src
