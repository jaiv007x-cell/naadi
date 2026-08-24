"""H.a/H.b regrade service — projection digest gate → sealed grade → portable artifact."""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.audit.caller_kinds import assert_known_caller_kind
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import AUDIT_SINK_FAILURE_TOTAL
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger.models import (
    RegradeArtifactRow,
    RegradeAuditRow,
    SessionTranscriptProjectionRow,
)
from services.pratibimb.ledger_read.errors import ScopeDeniedError, TenantBoundaryError
from services.pratibimb.regrade.digest import compute_transcript_digest
from services.pratibimb.regrade.sealed_grade import GRADER_VERSION, grade_sealed
from services.pratibimb.regrade.sign import (
    DEFAULT_REGRADE_ISSUER_KEY_ID,
    build_portable_envelope,
)
from shared.schemas.ledger_read import ConsentScope

log = logging.getLogger(__name__)
_CALLER = "ncvet_regrader"
_SURFACE = "regrade"

# Same entropy floor as credentials.service.EVIDENCE_REF_ENTROPY_BYTES (I-G-8 / H.c pin 6).
REGRADE_ID_ENTROPY_BYTES = 32


class TranscriptProjectionNotFoundError(LookupError):
    pass


class TranscriptDigestMismatchError(RuntimeError):
    def __init__(self, *, expected: str, computed: str, regrade_id: str) -> None:
        self.expected = expected
        self.computed = computed
        self.regrade_id = regrade_id
        super().__init__("transcript_digest_mismatch")


class RegradeDuplicateError(RuntimeError):
    """UNIQUE (tenant, session, digest) lost after grader invoke (C1b invoked-then-lost)."""

    def __init__(self, *, regrade_id: str) -> None:
        self.regrade_id = regrade_id
        super().__init__("regrade_duplicate")


class RegradeArtifactNotFoundError(LookupError):
    pass


class RegradeService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
        key_id: str = DEFAULT_REGRADE_ISSUER_KEY_ID,
        fail_audit: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._key_id = key_id
        self._fail_audit = fail_audit

    def _write_audit(self, session: Session, row: RegradeAuditRow) -> None:
        assert_known_caller_kind(row.caller_kind)
        if self._fail_audit:
            AUDIT_SINK_FAILURE_TOTAL.labels(
                sink="primary",
                surface=_SURFACE,
                tenant_id=row.tenant_id,
            ).inc()
            raise AuditWriteError(
                "regrade_audit_unavailable",
                correlation_id=row.regrade_id,
            )
        session.add(row)

    async def regrade(
        self,
        *,
        auth: AuthContext,
        session_id: str,
        scope: ConsentScope,
    ) -> dict[str, Any]:
        if scope is not ConsentScope.NCVET_REGRADE_SESSION:
            raise ScopeDeniedError(
                required=frozenset({ConsentScope.NCVET_REGRADE_SESSION}),
                granted=frozenset({scope}),
            )
        assert_known_caller_kind(_CALLER)
        regrade_id = secrets.token_urlsafe(REGRADE_ID_ENTROPY_BYTES)
        initiated = self._now()

        with self._session_factory() as session:
            proj = session.execute(
                select(SessionTranscriptProjectionRow).where(
                    SessionTranscriptProjectionRow.session_id == session_id
                )
            ).scalar_one_or_none()
            if proj is None:
                raise TranscriptProjectionNotFoundError(session_id)
            if proj.tenant_id != auth.tenant_id:
                raise TenantBoundaryError(
                    requested=proj.tenant_id,
                    auth_tenant=auth.tenant_id,
                )

            payload = json.loads(proj.payload_json)
            computed, digest_alg = compute_transcript_digest(payload)
            expected = proj.transcript_digest
            match = computed == expected

            if not match:
                completed = self._now()
                audit = RegradeAuditRow(
                    regrade_id=regrade_id,
                    tenant_id=auth.tenant_id,
                    session_id=session_id,
                    caller_kind=_CALLER,
                    caller_pseudo_id=auth.subject_pseudo_id,
                    scope=scope.value,
                    transcript_digest_expected=expected,
                    transcript_digest_computed=computed,
                    digest_match=False,
                    grader_invoked=False,
                    grader_outcome="not_invoked",
                    regrade_artifact_id=None,
                    error_kind="transcript_digest_mismatch",
                    initiated_at=initiated,
                    completed_at=completed,
                )
                try:
                    self._write_audit(session, audit)
                    session.commit()
                except AuditWriteError:
                    session.rollback()
                    raise
                raise TranscriptDigestMismatchError(
                    expected=expected, computed=computed, regrade_id=regrade_id
                )

            # I-H-11: digest asserted before grader runs.
            grade_payload = grade_sealed(
                payload=payload,
                rubric_schema_version=proj.rubric_schema_version,
                transcript_digest=expected,
            )
            signed_at = self._now()
            envelope = build_portable_envelope(
                regrade_id=regrade_id,
                transcript_digest=expected,
                grade_payload=grade_payload,
                grader_version=GRADER_VERSION,
                rubric_schema_version=proj.rubric_schema_version,
                signed_at=signed_at,
                key_id=self._key_id,
                digest_alg=digest_alg,
            )
            artifact = RegradeArtifactRow(
                regrade_id=regrade_id,
                tenant_id=auth.tenant_id,
                session_id=session_id,
                transcript_digest=expected,
                grader_version=GRADER_VERSION,
                rubric_schema_version=proj.rubric_schema_version,
                artifact_schema_version=envelope["artifact_schema_version"],
                regrade_issuer_key_id=self._key_id,
                signed_at=signed_at,
                valid_until=datetime.fromisoformat(
                    envelope["valid_until"].replace("Z", "+00:00")
                ),
                envelope_bytes=json.dumps(envelope, sort_keys=True, default=str),
                created_at=signed_at,
            )
            completed = self._now()
            audit = RegradeAuditRow(
                regrade_id=regrade_id,
                tenant_id=auth.tenant_id,
                session_id=session_id,
                caller_kind=_CALLER,
                caller_pseudo_id=auth.subject_pseudo_id,
                scope=scope.value,
                transcript_digest_expected=expected,
                transcript_digest_computed=computed,
                digest_match=True,
                grader_invoked=True,
                grader_outcome="success",
                regrade_artifact_id=regrade_id,
                error_kind=None,
                initiated_at=initiated,
                completed_at=completed,
            )
            try:
                self._write_audit(session, audit)
                session.add(artifact)
                session.commit()
            except AuditWriteError:
                session.rollback()
                raise
            except IntegrityError:
                # C1b invoked-then-lost: grader already ran; UNIQUE lost on INSERT.
                session.rollback()
                dup_audit = RegradeAuditRow(
                    regrade_id=regrade_id,
                    tenant_id=auth.tenant_id,
                    session_id=session_id,
                    caller_kind=_CALLER,
                    caller_pseudo_id=auth.subject_pseudo_id,
                    scope=scope.value,
                    transcript_digest_expected=expected,
                    transcript_digest_computed=computed,
                    digest_match=True,
                    grader_invoked=True,
                    grader_outcome="success",
                    regrade_artifact_id=None,
                    error_kind="regrade_duplicate",
                    initiated_at=initiated,
                    completed_at=self._now(),
                )
                with self._session_factory() as audit_session:
                    audit_session.add(dup_audit)
                    audit_session.commit()
                raise RegradeDuplicateError(regrade_id=regrade_id) from None
            return envelope

    async def fetch_artifact(
        self,
        *,
        auth: AuthContext,
        regrade_id: str,
        scope: ConsentScope,
    ) -> dict[str, Any]:
        if scope is not ConsentScope.NCVET_VERIFY_REGRADE:
            raise ScopeDeniedError(
                required=frozenset({ConsentScope.NCVET_VERIFY_REGRADE}),
                granted=frozenset({scope}),
            )
        with self._session_factory() as session:
            row = session.get(RegradeArtifactRow, regrade_id)
            if row is None or row.tenant_id != auth.tenant_id:
                raise RegradeArtifactNotFoundError(regrade_id)
            return json.loads(row.envelope_bytes)
