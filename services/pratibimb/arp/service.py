"""I.a ARP mint service — projection digest → eligibility → alternate grade → portable ARP."""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from services.pratibimb.arp.sealed_grade import GRADER_VERSION, grade_sealed_alternate
from services.pratibimb.arp.sign import (
    DEFAULT_ARP_ISSUER_KEY_ID,
    build_portable_arp_envelope,
)
from services.pratibimb.audit.caller_kinds import assert_known_caller_kind
from services.pratibimb.audit.errors import AuditWriteError
from services.pratibimb.audit.metrics import AUDIT_SINK_FAILURE_TOTAL
from services.pratibimb.auth.context import AuthContext
from services.pratibimb.ledger.models import (
    ArpArtifactRow,
    ArpAuditRow,
    ArpEligibleRubricRow,
    SessionTranscriptProjectionRow,
)
from services.pratibimb.ledger_read.errors import ScopeDeniedError, TenantBoundaryError
from services.pratibimb.regrade.digest import compute_transcript_digest
from shared.schemas.ledger_read import ConsentScope

log = logging.getLogger(__name__)
_CALLER = "ncvet_recompute"
_SURFACE = "arp"  # I.c: dedicated surface; historical regrade-labeled samples unchanged
ARP_ID_ENTROPY_BYTES = 32


class TranscriptProjectionNotFoundError(LookupError):
    pass


class TranscriptDigestMismatchError(RuntimeError):
    def __init__(self, *, expected: str, computed: str, arp_id: str) -> None:
        self.expected = expected
        self.computed = computed
        self.arp_id = arp_id
        super().__init__("transcript_digest_mismatch")


class ArpRubricIdentityCollisionError(RuntimeError):
    def __init__(self, *, arp_id: str) -> None:
        self.arp_id = arp_id
        super().__init__("arp_rubric_identity_collision")


class ArpRubricNotEligibleError(RuntimeError):
    def __init__(self, *, arp_id: str, error_kind: str = "alternate_rubric_unpublished") -> None:
        self.arp_id = arp_id
        self.error_kind = error_kind
        super().__init__(error_kind)


class ArpDuplicateError(RuntimeError):
    def __init__(self, *, arp_id: str) -> None:
        self.arp_id = arp_id
        super().__init__("arp_duplicate")


class ArpArtifactNotFoundError(LookupError):
    def __init__(self, arp_id: str) -> None:
        self.arp_id = arp_id
        super().__init__(arp_id)


class ArpDigestMismatchError(RuntimeError):
    """Presented envelope digest ≠ stored artifact digest → HTTP 422 (I.b pin 10)."""

    def __init__(
        self,
        *,
        arp_id: str,
        presented_digest: str,
        stored_digest: str,
    ) -> None:
        self.arp_id = arp_id
        self.presented_digest = presented_digest
        self.stored_digest = stored_digest
        super().__init__("digest_mismatch")


class UpdateCounter:
    """SQLAlchemy listener counting UPDATE/DELETE on a table (I-I-5 / C3)."""

    def __init__(self, table_name: str) -> None:
        self.table_name = table_name
        self.updates = 0
        self.deletes = 0
        self._session: Session | None = None

    def attach(self, session: Session) -> None:
        self._session = session
        event.listen(session, "before_flush", self._before_flush)

    def detach(self) -> None:
        if self._session is not None:
            event.remove(self._session, "before_flush", self._before_flush)
            self._session = None

    def _before_flush(self, session: Session, _flush_context, _instances) -> None:
        for obj in session.dirty:
            table = getattr(getattr(obj, "__table__", None), "name", None)
            if table == self.table_name:
                self.updates += 1
        for obj in session.deleted:
            table = getattr(getattr(obj, "__table__", None), "name", None)
            if table == self.table_name:
                self.deletes += 1


class ArpService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
        key_id: str = DEFAULT_ARP_ISSUER_KEY_ID,
        fail_audit: bool = False,
        unpublish_hook: Callable[[], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._key_id = key_id
        self._fail_audit = fail_audit
        # Test hook: runs after eligibility read, before grader (mid-flight unpublish).
        self._unpublish_hook = unpublish_hook

    def _write_audit(self, session: Session, row: ArpAuditRow) -> None:
        assert_known_caller_kind(row.caller_kind)
        if self._fail_audit:
            AUDIT_SINK_FAILURE_TOTAL.labels(
                sink="primary",
                surface=_SURFACE,
                tenant_id=row.tenant_id,
            ).inc()
            raise AuditWriteError(
                "arp_audit_unavailable",
                correlation_id=row.arp_id,
            )
        session.add(row)

    def _load_eligible(
        self,
        session: Session,
        *,
        tenant_id: str,
        alternate_rubric_id: str,
        alternate_rubric_version: str | None,
    ) -> ArpEligibleRubricRow | None:
        q = select(ArpEligibleRubricRow).where(
            ArpEligibleRubricRow.tenant_id == tenant_id,
            ArpEligibleRubricRow.alternate_rubric_id == alternate_rubric_id,
        )
        if alternate_rubric_version:
            q = q.where(
                ArpEligibleRubricRow.alternate_rubric_version == alternate_rubric_version
            )
        rows = list(session.execute(q).scalars())
        if not rows:
            return None
        # Prefer exact version match; else latest published eligible.
        for row in rows:
            if row.published and row.recompute_eligible:
                return row
        return None

    async def recompute(
        self,
        *,
        auth: AuthContext,
        session_id: str,
        alternate_rubric_id: str,
        scope: ConsentScope,
        alternate_rubric_version: str | None = None,
        regrade_id_cite: str | None = None,
        credential_id_cite: str | None = None,
    ) -> dict[str, Any]:
        if scope is not ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC:
            raise ScopeDeniedError(
                required=frozenset({ConsentScope.NCVET_RECOMPUTE_ALTERNATE_RUBRIC}),
                granted=frozenset({scope}),
            )
        assert_known_caller_kind(_CALLER)
        arp_id = secrets.token_urlsafe(ARP_ID_ENTROPY_BYTES)
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
            sealed_rubric_id = str(payload.get("rubric_id") or proj.rubric_schema_version)
            transcript_rubric_sv = proj.rubric_schema_version
            match = computed == expected

            def _fail_audit(
                *,
                error_kind: str,
                grader_invoked: bool = False,
                digest_match: bool = match,
                alt_ver: str | None = alternate_rubric_version,
                authority: str | None = None,
            ) -> None:
                completed = self._now()
                audit = ArpAuditRow(
                    arp_id=arp_id,
                    tenant_id=auth.tenant_id,
                    session_id=session_id,
                    caller_kind=_CALLER,
                    caller_pseudo_id=auth.subject_pseudo_id,
                    scope=scope.value,
                    transcript_digest_expected=expected,
                    transcript_digest_computed=computed,
                    digest_match=digest_match,
                    alternate_rubric_id=alternate_rubric_id,
                    alternate_rubric_version=alt_ver,
                    authority_id=authority,
                    transcript_rubric_schema_version=transcript_rubric_sv,
                    sealed_rubric_id=sealed_rubric_id,
                    grader_invoked=grader_invoked,
                    grader_outcome="not_invoked" if not grader_invoked else "error",
                    arp_artifact_id=None,
                    error_kind=error_kind,
                    initiated_at=initiated,
                    completed_at=completed,
                )
                try:
                    self._write_audit(session, audit)
                    session.commit()
                except AuditWriteError:
                    session.rollback()
                    raise

            if not match:
                _fail_audit(error_kind="transcript_digest_mismatch")
                raise TranscriptDigestMismatchError(
                    expected=expected, computed=computed, arp_id=arp_id
                )

            # I-I-16: collision pre-grader
            if alternate_rubric_id == sealed_rubric_id:
                _fail_audit(error_kind="arp_rubric_identity_collision")
                raise ArpRubricIdentityCollisionError(arp_id=arp_id)

            # Eligibility at grader invocation (transactional re-assert before write).
            eligible = self._load_eligible(
                session,
                tenant_id=auth.tenant_id,
                alternate_rubric_id=alternate_rubric_id,
                alternate_rubric_version=alternate_rubric_version,
            )
            if eligible is None:
                _fail_audit(error_kind="rubric_not_recompute_eligible")
                raise ArpRubricNotEligibleError(
                    arp_id=arp_id, error_kind="rubric_not_recompute_eligible"
                )

            if self._unpublish_hook is not None:
                self._unpublish_hook()

            # Re-assert eligibility in same session before grader (C2).
            session.expire_all()
            eligible = self._load_eligible(
                session,
                tenant_id=auth.tenant_id,
                alternate_rubric_id=alternate_rubric_id,
                alternate_rubric_version=alternate_rubric_version
                or (eligible.alternate_rubric_version if eligible else None),
            )
            if eligible is None or not eligible.published or not eligible.recompute_eligible:
                _fail_audit(error_kind="alternate_rubric_unpublished")
                raise ArpRubricNotEligibleError(
                    arp_id=arp_id, error_kind="alternate_rubric_unpublished"
                )

            alt_ver = eligible.alternate_rubric_version
            authority = eligible.authority_id
            score_payload = grade_sealed_alternate(
                payload=payload,
                alternate_rubric_id=alternate_rubric_id,
                alternate_rubric_version=alt_ver,
                transcript_digest=expected,
            )
            signed_at = self._now()
            envelope = build_portable_arp_envelope(
                arp_id=arp_id,
                transcript_digest=expected,
                digest_alg=digest_alg,
                score_payload=score_payload,
                grader_version=GRADER_VERSION,
                sealed_rubric_id=sealed_rubric_id,
                transcript_rubric_schema_version=transcript_rubric_sv,
                alternate_rubric_id=alternate_rubric_id,
                alternate_rubric_version=alt_ver,
                authority_id=authority,
                signed_at=signed_at,
                key_id=self._key_id,
                regrade_id_cite=regrade_id_cite,
                credential_id_cite=credential_id_cite,
            )
            artifact = ArpArtifactRow(
                arp_id=arp_id,
                tenant_id=auth.tenant_id,
                session_id=session_id,
                transcript_digest=expected,
                alternate_rubric_id=alternate_rubric_id,
                alternate_rubric_version=alt_ver,
                authority_id=authority,
                sealed_rubric_id=sealed_rubric_id,
                transcript_rubric_schema_version=transcript_rubric_sv,
                grader_version=GRADER_VERSION,
                artifact_schema_version=envelope["artifact_schema_version"],
                arp_issuer_key_id=self._key_id,
                signed_at=signed_at,
                valid_until=datetime.fromisoformat(
                    envelope["valid_until"].replace("Z", "+00:00")
                ),
                envelope_bytes=json.dumps(envelope, sort_keys=True, default=str),
                created_at=signed_at,
            )
            completed = self._now()
            audit = ArpAuditRow(
                arp_id=arp_id,
                tenant_id=auth.tenant_id,
                session_id=session_id,
                caller_kind=_CALLER,
                caller_pseudo_id=auth.subject_pseudo_id,
                scope=scope.value,
                transcript_digest_expected=expected,
                transcript_digest_computed=computed,
                digest_match=True,
                alternate_rubric_id=alternate_rubric_id,
                alternate_rubric_version=alt_ver,
                authority_id=authority,
                transcript_rubric_schema_version=transcript_rubric_sv,
                sealed_rubric_id=sealed_rubric_id,
                grader_invoked=True,
                grader_outcome="success",
                arp_artifact_id=arp_id,
                error_kind=None,
                initiated_at=initiated,
                completed_at=completed,
            )
            try:
                counter = UpdateCounter("regrade_artifacts")
                counter.attach(session)
                try:
                    self._write_audit(session, audit)
                    session.add(artifact)
                    session.commit()
                finally:
                    counter.detach()
                # I-I-5 structural belt: ARP mint must never UPDATE regrade_artifacts.
                assert counter.updates == 0 and counter.deletes == 0, (
                    "ARP mint must not UPDATE/DELETE regrade_artifacts"
                )
            except AuditWriteError:
                session.rollback()
                raise
            except IntegrityError:
                session.rollback()
                dup = ArpAuditRow(
                    arp_id=arp_id,
                    tenant_id=auth.tenant_id,
                    session_id=session_id,
                    caller_kind=_CALLER,
                    caller_pseudo_id=auth.subject_pseudo_id,
                    scope=scope.value,
                    transcript_digest_expected=expected,
                    transcript_digest_computed=computed,
                    digest_match=True,
                    alternate_rubric_id=alternate_rubric_id,
                    alternate_rubric_version=alt_ver,
                    authority_id=authority,
                    transcript_rubric_schema_version=transcript_rubric_sv,
                    sealed_rubric_id=sealed_rubric_id,
                    grader_invoked=True,
                    grader_outcome="success",
                    arp_artifact_id=None,
                    error_kind="arp_duplicate",
                    initiated_at=initiated,
                    completed_at=self._now(),
                )
                self._write_audit(session, dup)
                session.commit()
                raise ArpDuplicateError(arp_id=arp_id) from None
            return envelope

    async def fetch_artifact(
        self,
        *,
        auth: AuthContext,
        arp_id: str,
        scope: ConsentScope,
    ) -> dict[str, Any]:
        if scope is not ConsentScope.NCVET_VERIFY_ARP:
            raise ScopeDeniedError(
                required=frozenset({ConsentScope.NCVET_VERIFY_ARP}),
                granted=frozenset({scope}),
            )
        with self._session_factory() as session:
            row = session.get(ArpArtifactRow, arp_id)
            if row is None or row.tenant_id != auth.tenant_id:
                raise ArpArtifactNotFoundError(arp_id)
            return json.loads(row.envelope_bytes)

    def stored_digest_for(
        self,
        *,
        auth: AuthContext,
        arp_id: str,
    ) -> str | None:
        with self._session_factory() as session:
            row = session.get(ArpArtifactRow, arp_id)
            if row is None or row.tenant_id != auth.tenant_id:
                return None
            return row.transcript_digest
