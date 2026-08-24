"""I-S-11 — preceptor bias remediation state machine (versioned).

Named exclusion: no ``resolved`` / ``cleared`` state.
``removed`` = attest-pool exit (terminal) — not "bias fixed, stop watching."
Continuous cycle: retraining → active → down_weighted (re-escalation).
``removed`` reachable only from ``under_review`` (explicit ladder exit).
``retraining`` is recovery-only: success → active; fail/refuse → under_review (not removed).
"""
from __future__ import annotations

from typing import Final

BIAS_REMEDIATION_WORKFLOW_VERSION: Final[str] = "bias_remediation_workflow.v1"

STATE_ACTIVE: Final[str] = "active"
STATE_DOWN_WEIGHTED: Final[str] = "down_weighted"
STATE_UNDER_REVIEW: Final[str] = "under_review"
STATE_RETRAINING: Final[str] = "retraining"
STATE_REMOVED: Final[str] = "removed"

FORBIDDEN_STATES: Final[frozenset[str]] = frozenset({"resolved", "cleared"})

# Distinct forensic kind — SOC filter, not text-match on messages (H.b.1 discipline)
ERROR_KIND_ILLEGAL_TRANSITION: Final[str] = "illegal_transition"
ERROR_KIND_FORBIDDEN_STATE: Final[str] = "forbidden_state"

ALLOWED_TRANSITIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        (STATE_ACTIVE, STATE_DOWN_WEIGHTED),
        (STATE_DOWN_WEIGHTED, STATE_UNDER_REVIEW),
        (STATE_UNDER_REVIEW, STATE_RETRAINING),
        (STATE_UNDER_REVIEW, STATE_REMOVED),
        (STATE_RETRAINING, STATE_ACTIVE),
        (STATE_RETRAINING, STATE_UNDER_REVIEW),
    }
)


class BiasRemediationError(ValueError):
    """Bias state-machine failure with a SOC-filterable ``error_kind``."""

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        from_state: str,
        to_state: str,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.from_state = from_state
        self.to_state = to_state


def assert_valid_transition(from_state: str, to_state: str) -> None:
    if to_state in FORBIDDEN_STATES or from_state in FORBIDDEN_STATES:
        raise BiasRemediationError(
            f"forbidden bias state {from_state!r}→{to_state!r}; "
            f"named exclusion: no resolved/cleared (I-S-11)",
            error_kind=ERROR_KIND_FORBIDDEN_STATE,
            from_state=from_state,
            to_state=to_state,
        )
    if (from_state, to_state) not in ALLOWED_TRANSITIONS:
        raise BiasRemediationError(
            f"invalid bias remediation transition {from_state!r} → {to_state!r}; "
            f"allowed={sorted(ALLOWED_TRANSITIONS)}",
            error_kind=ERROR_KIND_ILLEGAL_TRANSITION,
            from_state=from_state,
            to_state=to_state,
        )
