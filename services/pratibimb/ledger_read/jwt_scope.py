"""
JWT scope resolution (MVP).

ConsentScope comes from signed JWT claims. Short TTL + refresh handles
revocation until ConsentServiceResolver replaces JwtConsentResolver.
"""
from __future__ import annotations

from shared.schemas.ledger_read import ConsentScope

from services.pratibimb.ledger_read.errors import ScopeDeniedError


def resolve_learner_scope(
    caller_id: str,
    learner_pseudo_id: str,
    scopes: frozenset[ConsentScope],
) -> ConsentScope:
    if ConsentScope.SELF_LEARNER in scopes and caller_id == learner_pseudo_id:
        return ConsentScope.SELF_LEARNER
    if ConsentScope.PRECEPTOR_REVIEW in scopes:
        return ConsentScope.PRECEPTOR_REVIEW
    raise ScopeDeniedError(
        required=frozenset({ConsentScope.SELF_LEARNER, ConsentScope.PRECEPTOR_REVIEW}),
        granted=scopes,
    )


def resolve_aggregate_scope(
    scopes: frozenset[ConsentScope],
    *,
    research: bool = False,
) -> ConsentScope:
    required = (
        ConsentScope.RESEARCH_DEIDENTIFIED
        if research
        else ConsentScope.AGGREGATE_ANALYTICS
    )
    if required in scopes:
        return required
    raise ScopeDeniedError(required=frozenset({required}), granted=scopes)
