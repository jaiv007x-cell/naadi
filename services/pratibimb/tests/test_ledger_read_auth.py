"""JWT scope resolution tests."""

from __future__ import annotations

import pytest

from services.pratibimb.ledger_read.errors import ScopeDeniedError
from services.pratibimb.ledger_read.jwt_scope import (
    resolve_aggregate_scope,
    resolve_learner_scope,
)
from shared.schemas.ledger_read import ConsentScope


def test_self_learner_requires_matching_sub():
    scope = resolve_learner_scope(
        "L1", "L1", frozenset({ConsentScope.SELF_LEARNER})
    )
    assert scope is ConsentScope.SELF_LEARNER


def test_self_learner_denied_for_other_learner():
    with pytest.raises(ScopeDeniedError):
        resolve_learner_scope(
            "L1", "L2", frozenset({ConsentScope.SELF_LEARNER})
        )


def test_preceptor_scope():
    scope = resolve_learner_scope(
        "preceptor-1", "L1", frozenset({ConsentScope.PRECEPTOR_REVIEW})
    )
    assert scope is ConsentScope.PRECEPTOR_REVIEW


def test_aggregate_scope():
    scope = resolve_aggregate_scope(frozenset({ConsentScope.AGGREGATE_ANALYTICS}))
    assert scope is ConsentScope.AGGREGATE_ANALYTICS
