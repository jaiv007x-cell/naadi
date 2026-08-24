"""Phase E2.1: project authoring published rows into runtime.published_case_corpus."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.pratibimb.authoring.constants import ApprovalKind, CaseWorkflowState
from services.pratibimb.authoring.models import (
    ApprovalEventRow,
    CaseDraftRow,
    GoldenFixtureRow,
    PublishedCaseVersionRow,
)
from services.pratibimb.authoring.state_machine import active_approval_subject_ids
from services.pratibimb.ledger.models import PublishedCaseCorpusRow


def canonical_envelope_dumps(envelope: dict[str, Any]) -> str:
    """
    Stable JSON text for corpus storage.

    Re-projection of an unchanged published row must be byte-identical
    (Pin 2). Always compare this string, never a parsed dict.
    """
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _json_default(obj: object) -> str:
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def derive_retire_flags(published: PublishedCaseVersionRow) -> tuple[str, bool]:
    """
    Pin 1: probe_only / workflow_state derived only from retired_at.

    Never accept a caller-supplied probe_only.
    """
    retired = published.retired_at is not None
    if retired:
        return CaseWorkflowState.RETIRED.value, True
    return CaseWorkflowState.PUBLISHED.value, False


class CorpusProjector:
    """
    Own-session writer for the runtime corpus (A10).

    Reads authoring via ``authoring_session``; writes corpus via ``corpus_session``.
    Safe to call after authoring commit — never wrap inside the publish txn.
    """

    def __init__(self, authoring_session: Session, corpus_session: Session) -> None:
        self._authoring = authoring_session
        self._corpus = corpus_session

    def project_one(self, published_id: str) -> PublishedCaseCorpusRow:
        published = self._authoring.get(PublishedCaseVersionRow, published_id)
        if published is None:
            raise KeyError(f"unknown published_case_version {published_id}")

        draft = self._authoring.get(CaseDraftRow, published.draft_id)
        if draft is None:
            raise KeyError(f"missing draft {published.draft_id} for published {published_id}")

        workflow_state, probe_only = derive_retire_flags(published)
        envelope = self._build_envelope(published, draft, workflow_state)
        envelope_text = canonical_envelope_dumps(envelope)
        now = _utc_now()

        row = self._corpus.get(PublishedCaseCorpusRow, published.id)
        if row is None:
            row = PublishedCaseCorpusRow(id=published.id)
            self._corpus.add(row)

        # A9: content_hash copied from published — never recomputed from envelope.
        row.tenant_id = published.tenant_id
        row.case_id = published.case_id
        row.version = published.version
        row.content_hash = published.content_hash
        row.assessment_mode = published.assessment_mode
        row.workflow_state = workflow_state
        row.probe_only = probe_only
        row.harness_version = published.harness_version
        row.published_at = published.published_at
        row.envelope_json = envelope_text
        row.projected_at = now

        self._corpus.flush()
        return row

    def get_by_triple(
        self,
        *,
        tenant_id: str,
        case_id: str,
        version: str,
    ) -> PublishedCaseCorpusRow | None:
        """Warm-path corpus read — never projects."""
        return self._corpus.scalars(
            select(PublishedCaseCorpusRow).where(
                PublishedCaseCorpusRow.tenant_id == tenant_id,
                PublishedCaseCorpusRow.case_id == case_id,
                PublishedCaseCorpusRow.version == version,
            )
        ).first()

    def project_on_miss(
        self,
        *,
        tenant_id: str,
        case_id: str,
        version: str,
    ) -> PublishedCaseCorpusRow | None:
        """
        I-E22-5 — Sole sync runtime→projector path.

        Closes the race between publish-commit and post-commit hook completion
        for an explicit ``(tenant_id, case_id, version)`` pick. Call ONLY after
        a corpus warm-path read returned nothing. Uses ``project_one`` — no
        duplicated projection logic.

        Returns None when authoring has no published row (genuine not-found).
        Returns the corpus row when authoring has the publish — whether the
        projection already existed or was written by this call.

        Do not add a second call site. The projection pipeline (hook +
        reconciler) is the authoritative write path; this exception exists
        solely for explicit-pick identity semantics. If you need another sync
        entry, you are probably solving the wrong problem.
        """
        from services.pratibimb.authoring.metrics import record_corpus_project_on_miss

        published = self._authoring.scalars(
            select(PublishedCaseVersionRow).where(
                PublishedCaseVersionRow.tenant_id == tenant_id,
                PublishedCaseVersionRow.case_id == case_id,
                PublishedCaseVersionRow.version == version,
            )
        ).first()
        if published is None:
            record_corpus_project_on_miss(outcome="not_found", tenant_id=tenant_id)
            return None

        row = self.project_one(published.id)
        self._corpus.commit()
        record_corpus_project_on_miss(outcome="projected", tenant_id=tenant_id)
        return row

    def refresh_now(self, tenant_id: str) -> int:
        """Project all published rows for ``tenant_id``. Idempotent. Returns count."""
        rows = list(
            self._authoring.scalars(
                select(PublishedCaseVersionRow).where(
                    PublishedCaseVersionRow.tenant_id == tenant_id
                )
            )
        )
        for published in rows:
            self.project_one(published.id)
        self._corpus.flush()
        return len(rows)

    def _build_envelope(
        self,
        published: PublishedCaseVersionRow,
        draft: CaseDraftRow,
        workflow_state: str,
    ) -> dict[str, Any]:
        fixtures = list(
            self._authoring.scalars(
                select(GoldenFixtureRow).where(GoldenFixtureRow.draft_id == draft.id)
            )
        )
        events = list(
            self._authoring.scalars(
                select(ApprovalEventRow).where(ApprovalEventRow.draft_id == draft.id)
            )
        )
        approvers = sorted(
            active_approval_subject_ids(events, kind=ApprovalKind.FORMATIVE)
            | active_approval_subject_ids(events, kind=ApprovalKind.SUMMATIVE)
        )
        fixture_map: dict[str, Any] = {}
        for fx in sorted(fixtures, key=lambda f: f.fixture_kind):
            fixture_map[fx.fixture_kind] = {
                "content_hash": fx.content_hash,
                "expected_grade": fx.expected_grade_json,
                "trace": fx.trace_json,
            }
        return {
            "approver_subject_ids": approvers,
            "blueprint": draft.blueprint_json,
            "fixtures": fixture_map,
            "harness_version": published.harness_version,
            "published_at": published.published_at,
            "workflow_state": workflow_state,
        }
