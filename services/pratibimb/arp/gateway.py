"""JWT gateway for I.a mint + I.b verify/fetch."""
from __future__ import annotations

from typing import Any

from services.pratibimb.arp.service import (
    ArpDigestMismatchError,
    ArpService,
)
from services.pratibimb.arp.verify import VerifyResult, offline_verify_arp
from services.pratibimb.arp.verify_audit import (
    FETCH_ARTIFACT_KIND,
    VERIFY_ASSIST_KIND,
    emit_arp_verify_audit,
)
from services.pratibimb.audit.sink import AuditSink
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger_read.auth import ConsentResolver


class JwtArpGateway:
    def __init__(
        self,
        service: ArpService,
        consent: ConsentResolver,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.service = service
        self.consent = consent
        self._audit = audit_sink

    async def recompute(
        self,
        auth: AuthContext,
        session_id: str,
        *,
        alternate_rubric_id: str,
        alternate_rubric_version: str | None = None,
        regrade_id_cite: str | None = None,
        credential_id_cite: str | None = None,
    ) -> dict[str, Any]:
        scope = await self.consent.for_ncvet_recompute_alternate_rubric(auth)
        return await self.service.recompute(
            auth=auth,
            session_id=session_id,
            alternate_rubric_id=alternate_rubric_id,
            alternate_rubric_version=alternate_rubric_version,
            scope=scope,
            regrade_id_cite=regrade_id_cite,
            credential_id_cite=credential_id_cite,
        )

    async def verify_assist(
        self,
        auth: AuthContext,
        envelope: dict[str, Any],
        *,
        request_id: str | None = None,
        status_list: dict[str, Any] | None = None,
    ) -> VerifyResult:
        scope = await self.consent.for_ncvet_verify_arp(auth)
        result = offline_verify_arp(envelope, status_list=status_list)
        presented = str(envelope.get("transcript_digest") or "")
        arp_id = str(envelope.get("arp_id") or "")
        stored = self.service.stored_digest_for(auth=auth, arp_id=arp_id) if arp_id else None

        if result.accepted and stored is not None and presented and presented != stored:
            if self._audit is not None:
                emit_arp_verify_audit(
                    self._audit,
                    auth=auth,
                    scope=scope.value,
                    query_kind=VERIFY_ASSIST_KIND,
                    params={
                        "arp_id": arp_id,
                        "presented_digest": presented,
                        "stored_digest": stored,
                        "accepted": False,
                        "reason": "digest_mismatch",
                    },
                    outcome="error",
                    error_kind="digest_mismatch",
                    request_id=request_id,
                    fail_closed=True,
                )
            raise ArpDigestMismatchError(
                arp_id=arp_id,
                presented_digest=presented,
                stored_digest=stored,
            )

        if self._audit is not None:
            emit_arp_verify_audit(
                self._audit,
                auth=auth,
                scope=scope.value,
                query_kind=VERIFY_ASSIST_KIND,
                params={
                    "arp_id": arp_id,
                    "presented_digest": presented or None,
                    "stored_digest": stored,
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
        arp_id: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        scope = await self.consent.for_ncvet_verify_arp(auth)
        envelope = await self.service.fetch_artifact(
            auth=auth,
            arp_id=arp_id,
            scope=scope,
        )
        stored = envelope.get("transcript_digest")
        if self._audit is not None:
            emit_arp_verify_audit(
                self._audit,
                auth=auth,
                scope=scope.value,
                query_kind=FETCH_ARTIFACT_KIND,
                params={
                    "arp_id": arp_id,
                    "presented_digest": stored,
                    "stored_digest": stored,
                },
                outcome="ok",
                request_id=request_id,
                fail_closed=True,
            )
        return envelope
