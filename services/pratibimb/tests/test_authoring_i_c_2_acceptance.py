"""I.c.2 acceptance marker inventory — distinct from ``i_acceptance`` base (58)."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.i_c_2_acceptance

_REPO = Path(__file__).resolve().parents[3]
_PHASE = _REPO / "docs" / "design" / "authoring_harness_phase_i.md"

_I_C_2_MODULES = (
    "services.pratibimb.tests.test_authoring_i_c_2_v2_only_ship_checklist",
    "services.pratibimb.tests.test_authoring_i_c_2",
)


def test_i_c_2_acceptance_modules_carry_marker():
    import importlib

    for mod_name in _I_C_2_MODULES:
        mod = importlib.import_module(mod_name)
        marks = getattr(mod, "pytestmark", [])
        if not isinstance(marks, list):
            marks = [marks]
        assert any(
            getattr(m, "name", None) == "i_c_2_acceptance" for m in marks
        ), f"{mod_name} missing pytestmark = pytest.mark.i_c_2_acceptance"


def test_i_c_2_acceptance_registered_in_pyproject():
    text = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "i_c_2_acceptance" in text


def test_i_c_2_1_belt_four_enumerated_in_phase_i():
    text = _PHASE.read_text(encoding="utf-8")
    assert "B1" in text and "B4" in text
    assert "i_c_2_acceptance" in text
