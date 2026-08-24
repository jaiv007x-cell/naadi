"""I.c.2 v2-only ship checklist belt — mechanical pins (i_c_2_acceptance bucket)."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.i_c_2_acceptance

_REPO = Path(__file__).resolve().parents[3]
_V2_SHIP = _REPO / "docs" / "ops" / "i_c_2_v2_only_ship_checklist.md"
_RUNBOOK = _REPO / "docs" / "ops" / "credentials_regrade_observability.md"
_PHASE = _REPO / "docs" / "design" / "authoring_harness_phase_i.md"


def test_i_c_2_b1_v2_only_annotation_template():
    text = _V2_SHIP.read_text(encoding="utf-8")
    assert "status-list-v2-only@<tag> (sha:<sha>)" in text
    assert "**Invalid near-variants:**" in text
    assert "`status_list_v2_only@…`" in text
    assert 'rg -n "status-list-v2-only@"' in text
    assert "all boxes ticked" in text.lower()


def test_i_c_2_b2_symmetric_negative_grep():
    text = _V2_SHIP.read_text(encoding="utf-8")
    assert "Symmetric negative greps" in text
    assert 'git show <v2-only-ship-commit> -- docs/ops/ | rg "arp-surface-cutover@"' in text
    assert "status-list-v2-only@" in text and "9c71dd4" in text


def test_i_c_2_b3_no_compat_delete_in_v2_only_scope():
    text = _V2_SHIP.read_text(encoding="utf-8")
    assert "normalize_status_list_entries|STATUS_LIST_COMPAT_COERCION_TOTAL" in text
    assert "no deletion/removal" in text.lower() or "No compat-delete" in text
    assert "**B3**" in text or "B3" in text


def test_i_c_2_phase_i_pins_and_marker():
    phase = _PHASE.read_text(encoding="utf-8")
    runbook = _RUNBOOK.read_text(encoding="utf-8")
    assert "credentials/compat.py" in phase
    assert "unknown_status_list_schema" in phase
    assert "i_c_2_acceptance" in phase
    assert "B1" in phase and "B4" in phase
    assert "test-page evidence" in phase.lower() or "test-page" in phase.lower()
    assert "status_list_schema_version" in runbook or "Forensic query" in runbook
