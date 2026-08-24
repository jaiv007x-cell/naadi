"""I.c ship checklist belt — mechanical pins for reviewer walk."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.i_acceptance

_REPO = Path(__file__).resolve().parents[3]
_SHIP = _REPO / "docs" / "ops" / "i_c_ship_checklist.md"
_COMPAT = _REPO / "docs" / "ops" / "i_c_2_compat_delete_checklist.md"
_FILLED = (
    "arp-surface-cutover@v2.14.0 (sha:f1fe486fe9fb048ac982e0a4d4b49ae293cb9797)"
)


def test_i_c_ship_checklist_exact_annotation_template():
    text = _SHIP.read_text(encoding="utf-8")
    assert _FILLED in text
    assert "**Invalid near-variants:**" in text
    assert "`arp-cutover@…`" in text
    assert "`arp_surface_cutover@…`" in text
    assert "merged 2026-08-24" in text
    assert "cleared 2026-08-24" in text
    assert "Ship confirmation posture" in text
    assert 'rg -n "arp-surface-cutover@"' in text


def test_i_c_ship_checklist_cutover_only_not_bundled_v2_only():
    text = _SHIP.read_text(encoding="utf-8")
    assert "cutover-only" in text.lower()
    assert "status-list-v2-only@" in text
    assert "9c71dd4" in text
    assert "separate deploy" in text.lower() or "separate diff" in text.lower()
    assert "May share a calendar day" in text or "may share a calendar day" in text


def test_i_c_2_compat_delete_test_grep_pattern():
    text = _COMPAT.read_text(encoding="utf-8")
    assert "normalize_status_list_entries|STATUS_LIST_COMPAT_COERCION_TOTAL" in text
    assert "services/pratibimb/tests/" in text
    assert "window-bounded" in text.lower() or "pre-reset" in text.lower()
    assert "First-walk" in text
    assert 'rg "arp-surface-cutover@"' in text
    assert "role:authoring-platform-oncall-lead" in text
