"""F.a/F.b: NCVET session evidence read — projection table only (I-F-1)."""

from __future__ import annotations



import json

from dataclasses import dataclass



from sqlalchemy import and_, or_, select

from sqlalchemy.orm import Session



from services.pratibimb.auth.context import AuthContext

from services.pratibimb.ledger.models import SessionEvidenceProjectionRow

from services.pratibimb.ledger_read.errors import InvalidNcvetCursorError, TenantBoundaryError

from services.pratibimb.ledger_read.ncvet_cursor import (

    NCVET_LIST_PAGE_MAX,

    decode_ncvet_cursor,

    encode_ncvet_cursor,

)

from shared.schemas.ledger_read import (

    NcvetLearnerSessionSummary,

    NcvetSessionEvidenceView,

)



# I-F-1: ncvet read service must not import SessionLedgerRow.





class SessionEvidenceNotFoundError(LookupError):

    """

    No projection row for session_id in tenant.



    Audit semantics (F.a): ``error_kind=not_found`` means the session_id is absent

    from ``runtime.session_evidence_projection``. ``result_row_count`` is NULL

    because no evidence rows were serialized to the client — not because the

    lookup query was skipped.

    """





@dataclass(frozen=True)

class LearnerSessionsFilter:

    tenant_id: str

    learner_pseudo_id: str

    limit: int = NCVET_LIST_PAGE_MAX

    cursor: str | None = None





@dataclass(frozen=True)

class NcvetListPageResult:

    items: list[NcvetLearnerSessionSummary]

    next_cursor: str | None = None

    page_ordinal: int = 1





def resolve_list_page_ordinal(

    cursor: str | None,

    *,

    learner_pseudo_id: str,

) -> tuple[int, tuple | None]:

    """

    I-F-7: ``page_ordinal`` is server-derived from cursor decode, never client input.



    Returns ``(page_ordinal, keyset_tuple | None)`` where keyset is

    ``(finalized_at_utc, session_id)`` for rows strictly before the cursor.

    """

    if cursor is None:

        return 1, None

    at, session_id, page_ordinal = decode_ncvet_cursor(

        cursor,

        learner_pseudo_id=learner_pseudo_id,

    )

    return page_ordinal, (at, session_id)





class NcvetEvidenceReadService:

    """Reads ``runtime.session_evidence_projection`` only — never ``session_ledger``."""



    def __init__(self, projection_session: Session) -> None:

        self._session = projection_session



    def get_session_evidence(

        self,

        session_id: str,

        *,

        auth: AuthContext,

        tenant_id: str | None = None,

    ) -> NcvetSessionEvidenceView:

        effective_tenant = tenant_id or auth.tenant_id

        if effective_tenant != auth.tenant_id:

            raise TenantBoundaryError(

                requested=effective_tenant,

                auth_tenant=auth.tenant_id,

            )

        row = self._session.scalars(

            select(SessionEvidenceProjectionRow).where(

                SessionEvidenceProjectionRow.session_id == session_id,

                SessionEvidenceProjectionRow.tenant_id == effective_tenant,

            )

        ).first()

        if row is None:

            raise SessionEvidenceNotFoundError(

                f"session evidence not found: session_id={session_id!r} "

                f"tenant={effective_tenant!r}"

            )

        return _row_to_view(row)



    def list_learner_sessions(

        self,

        filt: LearnerSessionsFilter,

        *,

        auth: AuthContext,

    ) -> NcvetListPageResult:

        if filt.tenant_id != auth.tenant_id:

            raise TenantBoundaryError(

                requested=filt.tenant_id,

                auth_tenant=auth.tenant_id,

            )

        if filt.limit < 1 or filt.limit > NCVET_LIST_PAGE_MAX:

            raise ValueError(f"limit must be 1..{NCVET_LIST_PAGE_MAX}")



        page_ordinal, keyset = resolve_list_page_ordinal(

            filt.cursor,

            learner_pseudo_id=filt.learner_pseudo_id,

        )



        stmt = select(SessionEvidenceProjectionRow).where(

            SessionEvidenceProjectionRow.tenant_id == filt.tenant_id,

            SessionEvidenceProjectionRow.learner_pseudo_id == filt.learner_pseudo_id,

        )

        if keyset is not None:

            after_at, after_sid = keyset

            stmt = stmt.where(

                or_(

                    SessionEvidenceProjectionRow.finalized_at_utc < after_at,

                    and_(

                        SessionEvidenceProjectionRow.finalized_at_utc == after_at,

                        SessionEvidenceProjectionRow.session_id < after_sid,

                    ),

                )

            )

        stmt = stmt.order_by(

            SessionEvidenceProjectionRow.finalized_at_utc.desc(),

            SessionEvidenceProjectionRow.session_id.desc(),

        ).limit(filt.limit + 1)



        rows = list(self._session.scalars(stmt))

        has_more = len(rows) > filt.limit

        page_rows = rows[: filt.limit]

        items = [_row_to_summary(row) for row in page_rows]

        next_cursor = None

        if has_more and page_rows:

            last = page_rows[-1]

            next_cursor = encode_ncvet_cursor(

                at=last.finalized_at_utc,

                session_id=last.session_id,

                learner_pseudo_id=filt.learner_pseudo_id,

                page_ordinal=page_ordinal + 1,

            )

        return NcvetListPageResult(

            items=items,

            next_cursor=next_cursor,

            page_ordinal=page_ordinal,

        )





def _row_to_summary(row: SessionEvidenceProjectionRow) -> NcvetLearnerSessionSummary:

    return NcvetLearnerSessionSummary(

        session_id=row.session_id,

        tenant_id=row.tenant_id,

        learner_pseudo_id=row.learner_pseudo_id,

        cohort_id=row.cohort_id,

        case_id=row.case_id,

        case_version=row.case_version,

        finalized_at_utc=row.finalized_at_utc,

        grade_total=row.grade_total,

        grade_passed=row.grade_passed,

        replay_hash=row.replay_hash,

        blueprint_content_hash=row.blueprint_content_hash,

        blueprint_source=row.blueprint_source,

    )





def _row_to_view(row: SessionEvidenceProjectionRow) -> NcvetSessionEvidenceView:

    case_context = json.loads(row.case_context_json)

    return NcvetSessionEvidenceView(

        session_id=row.session_id,

        tenant_id=row.tenant_id,

        learner_pseudo_id=row.learner_pseudo_id,

        cohort_id=row.cohort_id,

        case_id=row.case_id,

        case_version=row.case_version,

        finalized_at_utc=row.finalized_at_utc,

        physio_engine_version=row.physio_engine_version,

        rubric_version=row.rubric_version,

        replay_hash=row.replay_hash,

        blueprint_content_hash=row.blueprint_content_hash,

        blueprint_source=row.blueprint_source,

        grade_total=row.grade_total,

        grade_passed=row.grade_passed,

        axis_normalized=row.axis_normalized,

        evidence=row.evidence,

        flags=row.flags,

        actions=row.actions,

        case_context=case_context,

    )


