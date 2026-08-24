"""JWT gateway for H.a/H.b/H.c regrade."""
from __future__ import annotations

from typing import Any

from services.pratibimb.audit.sink import AuditSink
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.regrade.service import RegradeService
from services.pratibimb.regrade.verify import VerifyResult, offline_verify_regrade
from services.pratibimb.regrade.verify_audit import (
    FETCH_ARTIFACT_KIND,
    VERIFY_ASSIST_KIND,
    emit_regrade_verify_audit,
)
from shared.schemas.ledger_read import ConsentScope


class JwtRegradeGateway:
    def __init__(
        self,
        service: RegradeService,
        consent: ConsentResolver,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.service = service
        self.consent = consent
        self._audit = audit_sink

    async def regrade(
        self,
        auth: AuthContext,
        session_id: str,
    ) -> dict[str, Any]:
        scope = await self.consent.for_ncvet_regrade_session(auth)
        return await self.service.regrade(
            auth=auth,
            session_id=session_id,
            scope=scope,
        )

    async def verify_assist(
        self,
        auth: AuthContext,
        envelope: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> VerifyResult:
        scope = await self.consent.for_ncvet_verify_regrade(auth)
        result = offline_verify_regrade(envelope)
        # Emit-then-respond (H.c pin 4): audit before returning body to verifier.
        if self._audit is not None:
            emit_regrade_verify_audit(
                self._audit,
                auth=auth,
                scope=scope.value,
                query_kind=VERIFY_ASSIST_KIND,
                params={
                    "regrade_id": envelope.get("regrade_id"),
                    "accepted": result.accepted,
                    "reason": result.reason,
                },
                outcome="ok" if result.accepted else "error",
                error_kind=None if result.accepted else result.reason,
                request_id=request_id,
                fail_closed=True,
            )
        return result

    async def fetch_artifact(
        self,
        auth: AuthContext,
        regrade_id: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        scope = await self.consent.for_ncvet_verify_regrade(auth)
        envelope = await self.service.fetch_artifact(
            auth=auth,
            regrade_id=regrade_id,
            scope=scope,
        )
        # Emit-then-respond (H.c pin 4): audit before returning body to verifier.
        if self._audit is not None:
            emit_regrade_verify_audit(
                self._audit,
                auth=auth,
                scope=scope.value,
                query_kind=FETCH_ARTIFACT_KIND,
                params={"regrade_id": regrade_id},
                outcome="ok",
                request_id=request_id,
                fail_closed=True,
            )
        return envelope
