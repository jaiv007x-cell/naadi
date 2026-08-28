"""SAMVAAD.d runtime enforcement for the I-S-11 bias ladder."""
from __future__ import annotations

from services.pratibimb.samvaad.bias_remediation import assert_valid_transition


def detect_illegal_transition(from_state: str, to_state: str) -> None:
    """Raise the shared BiasRemediationError for an illegal runtime move."""
    assert_valid_transition(from_state, to_state)
