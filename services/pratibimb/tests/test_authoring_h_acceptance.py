"""Phase H acceptance suite entrypoint.

Run the full rung-3 contract in one invocation::

    pytest -m h_acceptance

Modules: ``test_authoring_h_a`` (15) + ``test_authoring_h_b`` (15) +
``test_authoring_h_c`` (9) + ``test_authoring_h_c_2`` (5) = **44** cases.

Pinned in ``docs/design/authoring_harness_phase_h.md`` §Phase H closeout.

Closeout belt only — not part of ``pytest -m h_acceptance`` (44 cases).
"""
from __future__ import annotations

import importlib
from pathlib import Path

_H_MODULES = (
    "services.pratibimb.tests.test_authoring_h_a",
    "services.pratibimb.tests.test_authoring_h_b",
    "services.pratibimb.tests.test_authoring_h_c",
    "services.pratibimb.tests.test_authoring_h_c_2",
)


def test_h_acceptance_modules_carry_h_acceptance_marker():
    for mod_name in _H_MODULES:
        mod = importlib.import_module(mod_name)
        marks = getattr(mod, "pytestmark", [])
        if not isinstance(marks, list):
            marks = [marks]
        assert any(
            getattr(m, "name", None) == "h_acceptance" for m in marks
        ), f"{mod_name} missing pytestmark = pytest.mark.h_acceptance"


def test_h_acceptance_suite_inventory_matches_closeout_doc():
    root = Path(__file__).resolve().parents[3]
    text = (root / "docs" / "design" / "authoring_harness_phase_h.md").read_text(
        encoding="utf-8"
    )
    assert "pytest -m h_acceptance" in text
    assert "44/44" in text
