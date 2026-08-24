"""Closed enum for ``ledger_read_audit.caller_kind`` — extend by explicit phase work only."""
from __future__ import annotations

from typing import Final

# F.a: ncvet_audit (012). G.a: ncvet_issuer (014). G.b: ncvet_verifier (015).
# H.a: ncvet_regrader (016). SAMVAAD.a: samvaad_verifier (018).
# I.a: ncvet_recompute (019); ncvet_arp_verifier reserved in 019 CHECK (emitters I.b).
# Validated at import + emit time — must match ck_audit_caller_kind.
KNOWN_AUDIT_CALLER_KINDS: Final[frozenset[str]] = frozenset({
    "preceptor",
    "analyst",
    "service",
    "authoring",
    "ncvet_audit",
    "ncvet_issuer",
    "ncvet_verifier",
    "ncvet_regrader",
    "samvaad_verifier",
    "ncvet_recompute",
    "ncvet_arp_verifier",
})


def assert_known_caller_kind(caller_kind: str) -> None:
    if caller_kind not in KNOWN_AUDIT_CALLER_KINDS:
        raise ValueError(
            f"caller_kind {caller_kind!r} not in KNOWN_AUDIT_CALLER_KINDS; "
            "extend via migration + phase pin"
        )
