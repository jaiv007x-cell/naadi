"""
KNOWN_QUERY_KINDS — closed vocabulary of audit query_kind labels.

Deliberately a separate namespace from app.action_registry.KNOWN_ACTION_IDS:
  - KNOWN_ACTION_IDS  = clinical learner actions inside a case
                        (give_aspirin_325_chewed, call_rapid_response, ...)
  - KNOWN_QUERY_KINDS = ledger read audit categories
                        (learner_evidence, unit_safety_report, ...)

Do NOT merge these. They share a shape (string enum-ish) and nothing else.

Live emitters are registered in AuditingLedgerReadService._REGISTERED_KINDS.
Reserved kinds below are roadmap placeholders — add to _REGISTERED_KINDS when
their read endpoint ships.
"""
from __future__ import annotations

from typing import Final

# Kinds currently emitted by AuditingLedgerReadService (see audit_decorator.py).
LIVE_QUERY_KINDS: Final[frozenset[str]] = frozenset({
    "learner_evidence",
    "unit_safety_report",
    "aggregate_patterns",
    "cohort_summary",
    "list_cohort_sessions",
})

# E3: authoring catalog reads (closed enum — extend only by explicit phase work).
E3_AUTHORING_CATALOG_KINDS: Final[frozenset[str]] = frozenset({
    "get_published_case_versions",
    "get_retirement_history",
})

# F: NCVET regulator reads (closed enum — extend only by explicit phase work).
F_NCVET_QUERY_KINDS: Final[frozenset[str]] = frozenset({
    "get_session_evidence",
    "list_learner_sessions",
})

KNOWN_QUERY_KINDS: Final[frozenset[str]] = frozenset({
    # --- live (Slice 5 read-audit) ---
    "learner_evidence",
    "unit_safety_report",
    "aggregate_patterns",
    "cohort_summary",
    "list_cohort_sessions",
    # --- reserved: learner portal expansion (authoring harness + self-view) ---
    "learner_case_detail",      # slice: case detail read for learner dashboard
    "learner_progress",         # slice: longitudinal progress rollup
    # --- reserved: preceptor console ---
    "preceptor_review",         # slice: structured preceptor review bundle
    "preceptor_cohort_summary", # slice: preceptor-scoped cohort rollup (distinct from cohort_summary)
    # --- reserved: program admin ---
    "program_competency_rollup",  # slice: program-level competency dashboard
    # --- reserved: summative / regulator (summative-restricted middleware gates these) ---
    "regulator_export",         # slice: NCVET/regulator bulk export
    "insurer_aggregate",        # slice: insurer deidentified aggregate feed
    "ncvet_audit",              # slice: NCVET audit-trail replay endpoint
    # --- reserved: research ---
    "research_deidentified",    # slice: research cohort read (k-anon + DP noise)
    # --- E3: authoring catalog reads (live) ---
    "get_published_case_versions",
    "get_retirement_history",
    # F.a: NCVET session evidence (live)
    "get_session_evidence",
    # F.b: NCVET learner session list (live)
    "list_learner_sessions",
    # G.b: credential status-list fetch (credentials surface — not /v1/ledger/)
    "fetch_credential_status_list",
    # H.c: regrade verify callbacks (credentials/regrade surface — not F kinds)
    "verify_regrade_assist",
    "fetch_regrade_artifact",
})


class UnknownQueryKindError(RuntimeError):
    """Raised when the audit wrapper emits a query_kind outside the registry."""


def assert_known(query_kind: str) -> None:
    if query_kind not in KNOWN_QUERY_KINDS:
        raise UnknownQueryKindError(
            f"query_kind {query_kind!r} is not registered in "
            f"KNOWN_QUERY_KINDS. Add it explicitly; no free-text audit labels."
        )


def assert_e3_catalog_kind(query_kind: str) -> None:
    """E3 catalog kinds are a closed sub-enum of KNOWN_QUERY_KINDS."""
    assert_known(query_kind)
    if query_kind not in E3_AUTHORING_CATALOG_KINDS:
        raise UnknownQueryKindError(
            f"query_kind {query_kind!r} is not an E3 authoring catalog kind; "
            f"expected one of {sorted(E3_AUTHORING_CATALOG_KINDS)}"
        )


def assert_f_ncvet_kind(query_kind: str) -> None:
    """F.a NCVET kinds are a closed sub-enum of KNOWN_QUERY_KINDS."""
    assert_known(query_kind)
    if query_kind not in F_NCVET_QUERY_KINDS:
        raise UnknownQueryKindError(
            f"query_kind {query_kind!r} is not an F NCVET kind; "
            f"expected one of {sorted(F_NCVET_QUERY_KINDS)}"
        )
