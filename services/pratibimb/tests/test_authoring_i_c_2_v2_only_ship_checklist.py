"""I.c.2 v2-only ship checklist belt — mechanical pins for scope walk."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.i_acceptance

_REPO = Path(__file__).resolve().parents[3]
_V2_SHIP = _REPO / "docs" / "ops" / "i_c_2_v2_only_ship_checklist.md"
_RUNBOOK = _REPO / "docs" / "ops" / "credentials_regrade_observability.md"
_PHASE = _REPO / "docs" / "design" / "authoring_harness_phase_i.md"


def test_i_c_2_v2_only_ship_checklist_annotation_template():
    text = _V2_SHIP.read_text(encoding="utf-8")
    assert "status-list-v2-only@<tag> (sha:<sha>)" in text
    assert "**Invalid near-variants:**" in text
    assert "`status_list_v2_only@…`" in text
    assert 'rg -n "status-list-v2-only@"' in text
    assert "all boxes ticked" in text.lower()


def test_i_c_2_v2_only_ship_checklist_symmetric_negative_grep():
    text = _V2_SHIP.read_text(encoding="utf-8")
    assert 'git show <v2-only-ship-commit> -- docs/ops/ | rg "arp-surface-cutover@"' in text
    assert "before v2-only deploy merges" in text.lower()
    assert "arp-surface-cutover@v2.14.0" in text
    assert "does not" in text.lower() or "not" in text.lower()


def test_i_c_2_v2_only_ship_checklist_no_day30_delete_in_scope():
    text = _V2_SHIP.read_text(encoding="utf-8")
    assert "day-30" in text.lower()
    assert "normalize_status_list_entries|STATUS_LIST_COMPAT_COERCION_TOTAL" in text
    assert "no deletion/removal matches" in text.lower() or "Must return no deletion" in text


def test_i_c_2_v2_only_runbook_and_phase_i_scope_open():
    runbook = _RUNBOOK.read_text(encoding="utf-8")
    phase = _PHASE.read_text(encoding="utf-8")
    assert "i_c_2_v2_only_ship_checklist.md" in runbook
    assert "TBD at ship" in runbook
    assert "I.c.2.1" in phase
    assert "scope open" in phase.lower()
    assert "P1" in phase and "P3" in phase
