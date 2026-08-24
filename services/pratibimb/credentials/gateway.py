"""JWT gateway for G.a/G.b credential surfaces."""
from __future__ import annotations

from typing import Any

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.credentials.service import CredentialIssuerService
from services.pratibimb.credentials.status_service import CredentialStatusService
from services.pratibimb.credentials.verify import (
    KeyRecord,
    VerifyReject,
    VerifyResult,
    offline_verify,
)
from services.pratibimb.ledger_read.auth import ConsentResolver
from services.pratibimb.ledger_read.errors import ScopeDeniedError
from shared.schemas.ledger_read import NcvetSessionEvidenceView


class JwtCredentialGateway:
    def __init__(
        self,
        service: CredentialIssuerService,
        consent: ConsentResolver,
        *,
        audited_ncvet_for_denials,
        status_service: CredentialStatusService | None = None,
        keyring: dict[str, KeyRecord] | None = None,
    ) -> None:
        self.service = service
        self.consent = consent
        self.status = status_service
        self.keyring = keyring or {}
        # Scope/tenant denials still need an audit emit on a known F kind path;
        # issuer denials before F read use the cloned ncvet auditor.
        self._denial_audit = audited_ncvet_for_denials

    async def issue(
        self,
        auth: AuthContext,
        session_id: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        params = {"session_id": session_id, "tenant_id": auth.tenant_id}
        try:
            scope = await self.consent.for_ncvet_issue_credential(auth)
        except ScopeDeniedError:
            self._denial_audit.record_scope_denial(
                auth,
                "get_session_evidence",
                params,
                request_id=request_id,
            )
            raise
        return await self.service.issue(
            auth=auth,
            session_id=session_id,
            scope=scope,
            request_id=request_id,
        )

    async def fetch_evidence_by_ref(
        self,
        auth: AuthContext,
        evidence_ref: str,
        *,
        request_id: str | None = None,
    ) -> NcvetSessionEvidenceView:
        params = {"evidence_ref": evidence_ref, "tenant_id": auth.tenant_id}
        try:
            scope = await self.consent.for_ncvet_fetch_evidence_by_ref(auth)
        except ScopeDeniedError:
            self._denial_audit.record_scope_denial(
                auth,
                "get_session_evidence",
                params,
                request_id=request_id,
            )
            raise
        return await self.service.fetch_evidence_by_ref(
            auth=auth,
            evidence_ref=evidence_ref,
            scope=scope,
            request_id=request_id,
        )

    async def revoke(
        self,
        auth: AuthContext,
        credential_id: str,
    ) -> dict[str, Any]:
        assert self.status is not None
        scope = await self.consent.for_ncvet_revoke_credential(auth)
        return await self.status.revoke(
            auth=auth,
            credential_id=credential_id,
            scope=scope,
        )

    async def fetch_status_list(
        self,
        auth: AuthContext,
        *,
        cursor: str | None = None,
        limit: int = 20,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        assert self.status is not None
        scope = await self.consent.for_ncvet_fetch_status_list(auth)
        return await self.status.fetch_status_list(
            auth=auth,
            scope=scope,
            cursor=cursor,
            limit=limit,
            request_id=request_id,
        )

    async def verify_assist(
        self,
        auth: AuthContext,
        credential: dict[str, Any],
        status_list: dict[str, Any],
        *,
        option_b_staleness: bool = False,
        option_b_reason: str = "",
    ) -> VerifyResult:
        """HTTP verify assist — requires verify scope; runs offline machine locally."""
        await self.consent.for_ncvet_verify_credential(auth)
        try:
            return offline_verify(
                credential,
                status_list,
                keyring=self.keyring,
                option_b_staleness=option_b_staleness,
                option_b_reason=option_b_reason,
            )
        except VerifyReject as exc:
            return VerifyResult(accepted=False, reason=exc.reason)
