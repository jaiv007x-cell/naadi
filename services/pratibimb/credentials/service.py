"""G.a credential issue + evidence_ref fetch (I-G-1…8)."""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.auth.context import AuthContext
from services.pratibimb.credentials.digest import compute_projection_digest
from services.pratibimb.credentials.sign import DEFAULT_ISSUER_KEY_ID, sign_credential_claims
from services.pratibimb.ledger.models import CredentialLedgerRow
from services.pratibimb.ledger_read.errors import ScopeDeniedError, TenantBoundaryError
from services.pratibimb.ledger_read.ncvet_audit import AuditingNcvetReadService
from shared.schemas.ledger_read import ConsentScope, NcvetSessionEvidenceView

_ISSUER_CALLER = "ncvet_issuer"
_GRADING_OUTCOME_VERSION = "grading_outcome.v1"


# Opaque evidence_ref: 32 random bytes → urlsafe base64 (~43 chars).
# Guessability threshold: 256 bits of CSPRNG entropy (I-G-8).
EVIDENCE_REF_ENTROPY_BYTES = 32


class EvidenceRefNotFoundError(LookupError):
    """Ref unknown (never issued or wrong tenant)."""


class EvidenceRefRevokedError(LookupError):
    """Ref existed but ``evidence_ref_revoked_at`` is set (I-G-8)."""


class CredentialIssuerService:
    """
    Issues credentials by consuming audited F ``get_session_evidence`` only.

    Does not import projection ORM / grading / authoring live-draft (I-G-1, I-G-4).
    """

    def __init__(
        self,
        audited_ncvet: AuditingNcvetReadService,
        session_factory: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
        issuer_key_id: str = DEFAULT_ISSUER_KEY_ID,
    ) -> None:
        self._ncvet = audited_ncvet.with_caller_kind(_ISSUER_CALLER)
        self._session_factory = session_factory
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._issuer_key_id = issuer_key_id

    @staticmethod
    def _new_evidence_ref() -> str:
        # Opaque, non-guessable — not derived from session_id (I-G-8).
        return secrets.token_urlsafe(EVIDENCE_REF_ENTROPY_BYTES)

    async def issue(
        self,
        *,
        auth: AuthContext,
        session_id: str,
        scope: ConsentScope,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if scope is not ConsentScope.NCVET_ISSUE_CREDENTIAL:
            raise ScopeDeniedError(
                required=frozenset({ConsentScope.NCVET_ISSUE_CREDENTIAL}),
                granted=frozenset({scope}),
            )
        view, audit_query_id, audit_fp = await self._ncvet.get_session_evidence_with_audit_meta(
            session_id,
            scope=scope,
            auth=auth,
            tenant_id=auth.tenant_id,
            request_id=request_id,
        )
        digest, digest_alg = compute_projection_digest(view)
        issued_at = self._now()
        evidence_ref = self._new_evidence_ref()
        blueprint_version = view.case_version

        with self._session_factory() as session:
            existing = list(
                session.execute(
                    select(CredentialLedgerRow)
                    .where(CredentialLedgerRow.tenant_id == auth.tenant_id)
                    .where(CredentialLedgerRow.session_id == session_id)
                    .order_by(CredentialLedgerRow.credential_version.desc())
                ).scalars()
            )
            for old in existing:
                if old.evidence_ref_revoked_at is None:
                    old.evidence_ref_revoked_at = issued_at
            credential_version = (existing[0].credential_version + 1) if existing else 1
            credential_id = existing[0].credential_id if existing else str(uuid.uuid4())

            claims = {
                "type": ["VerifiableCredential", "NaadiCompetencyCredential"],
                "credential_id": credential_id,
                "credential_version": credential_version,
                "evidence_ref": evidence_ref,
                "projection_digest": digest,
                "digest_alg": digest_alg,
                "audit_fingerprint": audit_fp,
                "audit_query_id": audit_query_id,
                "issued_at": issued_at.isoformat(),
                "issuer_key_id": self._issuer_key_id,
                "blueprint_version": blueprint_version,
                "grading_outcome_version": _GRADING_OUTCOME_VERSION,
                "tenant_id": view.tenant_id,
                "grade_passed": view.grade_passed,
                "grade_total": view.grade_total,
            }
            signed = sign_credential_claims(claims, key_id=self._issuer_key_id)
            assert "session_id" not in signed
            row = CredentialLedgerRow(
                credential_id=credential_id,
                credential_version=credential_version,
                tenant_id=auth.tenant_id,
                evidence_ref=evidence_ref,
                session_id=session_id,
                issued_at=issued_at,
                revoked_at=None,
                evidence_ref_revoked_at=None,
                projection_digest=digest,
                digest_alg=digest_alg,
                audit_query_id=audit_query_id,
                audit_fingerprint=audit_fp,
                blueprint_version=blueprint_version,
                grading_outcome_version=_GRADING_OUTCOME_VERSION,
                issuer_key_id=self._issuer_key_id,
                credential_bytes=json.dumps(signed, sort_keys=True, default=str),
            )
            session.add(row)
            session.commit()
        return signed

    async def fetch_evidence_by_ref(
        self,
        *,
        auth: AuthContext,
        evidence_ref: str,
        scope: ConsentScope,
        request_id: str | None = None,
    ) -> NcvetSessionEvidenceView:
        if scope is not ConsentScope.NCVET_FETCH_EVIDENCE_BY_REF:
            raise ScopeDeniedError(
                required=frozenset({ConsentScope.NCVET_FETCH_EVIDENCE_BY_REF}),
                granted=frozenset({scope}),
            )
        with self._session_factory() as session:
            row = session.execute(
                select(CredentialLedgerRow).where(
                    CredentialLedgerRow.evidence_ref == evidence_ref
                )
            ).scalar_one_or_none()
            if row is None:
                raise EvidenceRefNotFoundError(evidence_ref)
            if row.tenant_id != auth.tenant_id:
                raise TenantBoundaryError(
                    requested=row.tenant_id,
                    auth_tenant=auth.tenant_id,
                )
            if row.evidence_ref_revoked_at is not None:
                raise EvidenceRefRevokedError(evidence_ref)
            session_id = row.session_id
        return await self._ncvet.get_session_evidence(
            session_id,
            scope=scope,
            auth=auth,
            tenant_id=auth.tenant_id,
            request_id=request_id,
        )
