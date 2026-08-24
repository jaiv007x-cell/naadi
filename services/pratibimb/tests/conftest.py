"""Disable ledger side effects unless a test opts in."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_ledger_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_DISABLED", "1")
