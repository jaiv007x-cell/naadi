"""
Privacy policy for ledger read responses.

Maps internal failures to stable HTTP bodies without leaking sensitive
cohort sizes that could themselves be identifying.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from shared.schemas.ledger_read import PrivacyErrorBody


@dataclass(frozen=True)
class PrivacyPolicy:
    k_anon_floor: int = 50

    def insufficient_cohort_response(self) -> dict[str, Any]:
        return PrivacyErrorBody(
            error="insufficient_cohort",
            reason="minimum_cohort_size_not_met",
            minimum_required=self.k_anon_floor,
        ).model_dump()

    def consent_denied_response(self) -> dict[str, Any]:
        return PrivacyErrorBody(
            error="consent_denied",
            reason="consent_scope_not_granted",
        ).model_dump()

    def auth_required_response(self) -> dict[str, Any]:
        return PrivacyErrorBody(
            error="unauthorized",
            reason="ledger_read_not_permitted",
        ).model_dump()

    def unknown_unit_response(self) -> dict[str, Any]:
        return PrivacyErrorBody(
            error="not_found",
            reason="unit_not_found_or_inactive",
        ).model_dump()


def raise_privacy_http(status_code: int, body: dict[str, Any]) -> None:
    raise HTTPException(status_code=status_code, detail=body)
