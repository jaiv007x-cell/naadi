"""LEDGER_DISABLED startup policy tests."""

from __future__ import annotations

import json
import logging

import pytest

from services.pratibimb.ledger.startup import (
    LedgerStartupError,
    validate_ledger_startup,
)


def test_disabled_in_dev_no_loud_warning(caplog, monkeypatch, tmp_path):
    monkeypatch.setenv("LEDGER_DISABLED", "1")
    monkeypatch.setenv("ENV", "dev")
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([]), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        validate_ledger_startup(corpus_path=corpus)

    assert not any("NO EVIDENCE WILL BE PERSISTED" in r.message for r in caplog.records)


def test_disabled_in_staging_warns_loudly(caplog, monkeypatch, tmp_path):
    monkeypatch.setenv("LEDGER_DISABLED", "1")
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"case_id": "p1", "assessment_mode": "practice"}]), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        validate_ledger_startup(corpus_path=corpus)

    assert any("NO EVIDENCE WILL BE PERSISTED" in r.message for r in caplog.records)


def test_disabled_with_summative_refuses_boot(monkeypatch, tmp_path):
    monkeypatch.setenv("LEDGER_DISABLED", "1")
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps([
            {"case_id": "formative_1", "assessment_mode": "formative"},
            {"case_id": "summative_neet_pg_01", "assessment_mode": "summative"},
        ]),
        encoding="utf-8",
    )
    with pytest.raises(LedgerStartupError) as exc:
        validate_ledger_startup(corpus_path=corpus)
    assert "summative_neet_pg_01" in str(exc.value)


def test_enabled_never_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DISABLED", raising=False)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps([{"case_id": "x", "assessment_mode": "summative"}]),
        encoding="utf-8",
    )
    validate_ledger_startup(corpus_path=corpus)
