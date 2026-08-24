"""I.c.2.1 product belt — v2-only reader retirement (P1–P3)."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.i_c_2_acceptance

from services.pratibimb.arp.verify import (
    arp_unknown_schema_http_detail,
    offline_verify_arp,
)
from services.pratibimb.audit.metrics import STATUS_LIST_COMPAT_COERCION_TOTAL

_ARP_ROOT = Path(__file__).resolve().parents[1] / "arp"


def test_i_c_2_1_arp_v1_snapshot_fail_closed_no_coercion_counter():
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
    arp_src = "\n".join(p.read_text(encoding="utf-8") for p in _ARP_ROOT.glob("*.py"))
    assert "credentials.compat" not in arp_src
    assert "compat.normalize" not in arp_src
    assert "coerce_status_list_entries_for_verify" not in arp_src
