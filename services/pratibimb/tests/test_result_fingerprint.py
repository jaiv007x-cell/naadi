from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from services.pratibimb.app.action_registry import KNOWN_ACTION_IDS
from services.pratibimb.ledger_read.fingerprint import compute_result_fingerprint
from services.pratibimb.ledger_read.query_kinds import (
    KNOWN_QUERY_KINDS,
    UnknownQueryKindError,
    assert_known,
)


class _Resp(BaseModel):
    learner_pseudo_id: str
    score: float
    recorded_at: datetime


def test_fingerprint_stable_across_equivalent_pydantic_and_dict():
    ts = datetime(2026, 8, 19, 22, 0, tzinfo=timezone.utc)
    model = _Resp(learner_pseudo_id="p-1", score=0.82, recorded_at=ts)
    as_dict = model.model_dump(mode="json")
    assert compute_result_fingerprint(model) == compute_result_fingerprint(as_dict)


def test_fingerprint_stable_across_key_order():
    a = {"a": 1, "b": [1, 2, 3], "c": {"x": True, "y": None}}
    b = {"c": {"y": None, "x": True}, "b": [1, 2, 3], "a": 1}
    assert compute_result_fingerprint(a) == compute_result_fingerprint(b)


def test_fingerprint_changes_on_scalar_mutation():
    base = {"learner_pseudo_id": "p-1", "score": 0.82}
    mutated = {"learner_pseudo_id": "p-1", "score": 0.83}
    assert compute_result_fingerprint(base) != compute_result_fingerprint(mutated)


def test_fingerprint_changes_on_nested_mutation():
    base = {"hits": [{"id": "h1", "pts": 10}, {"id": "h2", "pts": 5}]}
    mutated = {"hits": [{"id": "h1", "pts": 10}, {"id": "h2", "pts": 6}]}
    assert compute_result_fingerprint(base) != compute_result_fingerprint(mutated)


def test_fingerprint_changes_on_list_reorder():
    a = {"hits": [{"id": "h1"}, {"id": "h2"}]}
    b = {"hits": [{"id": "h2"}, {"id": "h1"}]}
    assert compute_result_fingerprint(a) != compute_result_fingerprint(b)


def test_fingerprint_treats_set_as_sorted():
    a = {"tags": {"drug.admin", "vital.threshold"}}
    b = {"tags": {"vital.threshold", "drug.admin"}}
    assert compute_result_fingerprint(a) == compute_result_fingerprint(b)


def test_fingerprint_rejects_none():
    with pytest.raises(ValueError, match="denial path"):
        compute_result_fingerprint(None)


def test_fingerprint_is_hex_sha256():
    fp = compute_result_fingerprint({"x": 1})
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_known_query_kinds_contains_regulator_gated_kinds():
    for kind in ("regulator_export", "insurer_aggregate", "ncvet_audit"):
        assert kind in KNOWN_QUERY_KINDS


def test_assert_known_rejects_freetext():
    with pytest.raises(UnknownQueryKindError):
        assert_known("arbitrary_label_someone_invented")


def test_assert_known_accepts_registered():
    assert_known("learner_evidence")


def test_query_kinds_do_not_overlap_action_registry():
    assert KNOWN_QUERY_KINDS.isdisjoint(KNOWN_ACTION_IDS)
