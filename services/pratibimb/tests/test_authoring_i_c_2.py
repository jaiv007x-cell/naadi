"""I.c.2.1 product belt — v2-only reader retirement (P1–P3)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.i_c_2_acceptance


@pytest.mark.skip(reason="P3 not landed — opens with I.c.2.1 implementation")
def test_i_c_2_1_arp_v1_snapshot_fail_closed_no_coercion_counter():
    """B4 / P3 — ARP verify against v1 fixture: fail-closed, no coercion counter increment.

    Fixture: status_list.v1 envelope with credential_id-only entry (no identifier_kind).
    Assert:
      - offline_verify_arp → accepted=False, reason schema_version_unsupported (or equivalent)
      - STATUS_LIST_COMPAT_COERCION_TOTAL unchanged (credentials-side shim only)
    """
    raise NotImplementedError("land with P3")
