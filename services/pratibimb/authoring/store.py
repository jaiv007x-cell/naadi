"""Case draft persistence and state transitions."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from services.pratibimb.authoring.blueprint_io import content_hash_from_case_json
from services.pratibimb.authoring.compiler import CompilerErrors, compile_blueprint
from services.pratibimb.authoring.constants import (
    HARNESS_VERSION,
    SCOPE_ADMIN,
    ApprovalEventType,
    ApprovalKind,
    CaseWorkflowState,
    FixtureKind,
    SummativeGrantOutcome,
)
from services.pratibimb.authoring.dry_run import DryRunResult
from services.pratibimb.authoring.models import (
    ApprovalEventRow,
    AuthoringAuditRow,
    CaseDraftRow,
    CaseStateTransitionRow,
    GoldenFixtureRow,
    PublishedCaseVersionRow,
    RetiredCaseVersionRow,
)
from services.pratibimb.authoring.errors import (
    BlueprintValidationError,
    ContentHashDriftError,
    InvalidTransitionError,
    ScopeDeniedError,
)
from services.pratibimb.authoring.publish import (
    assessment_mode_from_blueprint,
    backfill_clinical_reviewer_if_needed,
    case_identity_from_draft,
    fixture_hashes,
)
from services.pratibimb.authoring.metrics import (
    record_publish,
    record_retire,
    set_published_active,
    set_retired_total,
)
from services.pratibimb.authoring.registry_snapshot import (
    RegistrySnapshot,
    capture_registry_snapshot,
)
from services.pratibimb.authoring.state_machine import (
    PublishResult,
    RetireResult,
    TransitionActor,
    SummativeApprovalResult,
    _required_scope_for,
    assert_fresh_dry_run_matches_submit,
    execute_draft_to_in_review,
    submit_dry_run_hash,
    summative_ready,
    validate_publish_preconditions,
    validate_retire_preconditions,
    validate_summative_approver,
    validate_transition_request,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _draft_content_hash(blueprint_json: dict) -> str:
    try:
        return content_hash_from_case_json(blueprint_json)
    except (KeyError, TypeError, ValueError):
        canonical = json.dumps(
            blueprint_json, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fixture_content_hash(trace_json: dict, expected_grade_json: dict) -> str:
    payload = {
        "trace": trace_json,
        "expect": expected_grade_json,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CaseDraftStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_draft(
        self,
        *,
        tenant_id: str,
        author_subject_id: str,
        blueprint_json: dict,
        blueprint_version: str,
        draft_id: str | None = None,
    ) -> CaseDraftRow:
        now = _utcnow()
        draft = CaseDraftRow(
            id=draft_id or str(uuid.uuid4()),
            tenant_id=tenant_id,
            author_subject_id=author_subject_id,
            blueprint_json=blueprint_json,
            blueprint_version=blueprint_version,
            created_at=now,
            updated_at=now,
            current_state=CaseWorkflowState.DRAFT.value,
            harness_version=HARNESS_VERSION,
        )
        self._session.add(draft)
        self._session.flush()
        return draft

    def update_draft(
        self,
        draft_id: str,
        *,
        blueprint_json: dict,
        blueprint_version: str,
    ) -> CaseDraftRow:
        draft = self.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"unknown draft {draft_id}")
        if draft.current_state != CaseWorkflowState.DRAFT.value:
            raise InvalidTransitionError(
                f"cannot edit draft in state {draft.current_state}"
            )
        draft.blueprint_json = blueprint_json
        draft.blueprint_version = blueprint_version
        draft.updated_at = _utcnow()
        self._session.flush()
        return draft

    def list_drafts_for_tenant(self, tenant_id: str) -> list[CaseDraftRow]:
        return list(
            self._session.execute(
                select(CaseDraftRow)
                .where(CaseDraftRow.tenant_id == tenant_id)
                .order_by(CaseDraftRow.updated_at.desc())
            ).scalars()
        )

    def upsert_fixture(
        self,
        draft_id: str,
        *,
        fixture_kind: FixtureKind,
        trace_json: dict,
        expected_grade_json: dict,
    ) -> GoldenFixtureRow:
        draft = self.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"unknown draft {draft_id}")
        if draft.current_state != CaseWorkflowState.DRAFT.value:
            raise InvalidTransitionError(
                f"cannot edit fixtures in state {draft.current_state}"
            )
        content_hash = _fixture_content_hash(trace_json, expected_grade_json)
        existing = self._session.execute(
            select(GoldenFixtureRow).where(
                GoldenFixtureRow.draft_id == draft_id,
                GoldenFixtureRow.fixture_kind == fixture_kind.value,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.trace_json = trace_json
            existing.expected_grade_json = expected_grade_json
            existing.content_hash = content_hash
            row = existing
        else:
            row = GoldenFixtureRow(
                id=str(uuid.uuid4()),
                draft_id=draft_id,
                fixture_kind=fixture_kind.value,
                trace_json=trace_json,
                expected_grade_json=expected_grade_json,
                content_hash=content_hash,
            )
            self._session.add(row)
        self._session.flush()
        return row

    def get_draft(self, draft_id: str) -> CaseDraftRow | None:
        return self._session.get(CaseDraftRow, draft_id)

    def list_fixtures(self, draft_id: str) -> list[GoldenFixtureRow]:
        return list(
            self._session.execute(
                select(GoldenFixtureRow).where(GoldenFixtureRow.draft_id == draft_id)
            ).scalars()
        )

    def list_transitions(self, draft_id: str) -> list[CaseStateTransitionRow]:
        return list(
            self._session.execute(
                select(CaseStateTransitionRow)
                .where(CaseStateTransitionRow.draft_id == draft_id)
                .order_by(CaseStateTransitionRow.occurred_at)
            ).scalars()
        )

    def list_approval_events(self, draft_id: str) -> list[ApprovalEventRow]:
        return list(
            self._session.execute(
                select(ApprovalEventRow)
                .where(ApprovalEventRow.draft_id == draft_id)
                .order_by(ApprovalEventRow.occurred_at)
            ).scalars()
        )

    def list_audit_events(self, draft_id: str) -> list[AuthoringAuditRow]:
        return list(
            self._session.execute(
                select(AuthoringAuditRow)
                .where(AuthoringAuditRow.draft_id == draft_id)
                .order_by(AuthoringAuditRow.at_utc)
            ).scalars()
        )

    def list_published_versions_for_case(self, case_id: str) -> list[PublishedCaseVersionRow]:
        return list(
            self._session.execute(
                select(PublishedCaseVersionRow)
                .where(PublishedCaseVersionRow.case_id == case_id)
                .order_by(PublishedCaseVersionRow.published_at)
            ).scalars()
        )

    def list_published_for_tenant_case(
        self,
        *,
        tenant_id: str,
        case_id: str,
        include_retired: bool = True,
    ) -> list[PublishedCaseVersionRow]:
        stmt = (
            select(PublishedCaseVersionRow)
            .where(
                PublishedCaseVersionRow.tenant_id == tenant_id,
                PublishedCaseVersionRow.case_id == case_id,
            )
            .order_by(PublishedCaseVersionRow.published_at)
        )
        rows = list(self._session.execute(stmt).scalars())
        if include_retired:
            return rows
        return [row for row in rows if row.retired_at is None]

    def list_published_for_tenant_case_page(
        self,
        *,
        tenant_id: str,
        case_id: str,
        include_retired: bool = True,
        limit: int = 100,
        after_published_at: datetime | None = None,
        after_id: str | None = None,
    ) -> list[PublishedCaseVersionRow]:
        stmt = (
            select(PublishedCaseVersionRow)
            .where(
                PublishedCaseVersionRow.tenant_id == tenant_id,
                PublishedCaseVersionRow.case_id == case_id,
            )
            .order_by(
                PublishedCaseVersionRow.published_at.desc(),
                PublishedCaseVersionRow.id.desc(),
            )
            .limit(limit)
        )
        if not include_retired:
            stmt = stmt.where(PublishedCaseVersionRow.retired_at.is_(None))
        if after_published_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    PublishedCaseVersionRow.published_at < after_published_at,
                    and_(
                        PublishedCaseVersionRow.published_at == after_published_at,
                        PublishedCaseVersionRow.id < after_id,
                    ),
                )
            )
        return list(self._session.execute(stmt).scalars())

    def get_published_version(
        self,
        *,
        tenant_id: str,
        case_id: str,
        version: str,
    ) -> PublishedCaseVersionRow | None:
        return self._session.execute(
            select(PublishedCaseVersionRow).where(
                PublishedCaseVersionRow.tenant_id == tenant_id,
                PublishedCaseVersionRow.case_id == case_id,
                PublishedCaseVersionRow.version == version,
            )
        ).scalars().first()

    def get_published_for_draft(self, draft_id: str) -> PublishedCaseVersionRow | None:
        return self._session.execute(
            select(PublishedCaseVersionRow)
            .where(PublishedCaseVersionRow.draft_id == draft_id)
            .order_by(PublishedCaseVersionRow.published_at.desc())
        ).scalars().first()

    def list_retired_for_draft(self, draft_id: str) -> list[RetiredCaseVersionRow]:
        return list(
            self._session.execute(
                select(RetiredCaseVersionRow)
                .where(RetiredCaseVersionRow.draft_id == draft_id)
                .order_by(RetiredCaseVersionRow.retired_at)
            ).scalars()
        )

    def list_retirement_history_for_tenant(
        self,
        *,
        tenant_id: str,
        case_id: str | None = None,
        limit: int = 100,
        after_retired_at: datetime | None = None,
        after_id: str | None = None,
    ) -> list[RetiredCaseVersionRow]:
        """Tenant-scoped retirement audit rows (keyset pagination — I-E3-7)."""
        stmt = (
            select(RetiredCaseVersionRow)
            .where(RetiredCaseVersionRow.tenant_id == tenant_id)
            .order_by(
                RetiredCaseVersionRow.retired_at.desc(),
                RetiredCaseVersionRow.id.desc(),
            )
            .limit(limit)
        )
        if case_id is not None:
            stmt = stmt.where(RetiredCaseVersionRow.case_id == case_id)
        if after_retired_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    RetiredCaseVersionRow.retired_at < after_retired_at,
                    and_(
                        RetiredCaseVersionRow.retired_at == after_retired_at,
                        RetiredCaseVersionRow.id < after_id,
                    ),
                )
            )
        return list(self._session.execute(stmt).scalars())

    def count_published_active(self, tenant_id: str) -> int:
        rows = self._session.execute(
            select(PublishedCaseVersionRow).where(
                PublishedCaseVersionRow.tenant_id == tenant_id,
                PublishedCaseVersionRow.retired_at.is_(None),
            )
        ).scalars().all()
        return len(list(rows))

    def count_retired(self, tenant_id: str) -> int:
        rows = self._session.execute(
            select(PublishedCaseVersionRow).where(
                PublishedCaseVersionRow.tenant_id == tenant_id,
                PublishedCaseVersionRow.retired_at.is_not(None),
            )
        ).scalars().all()
        return len(list(rows))

    def _refresh_publish_gauges(self, tenant_id: str) -> None:
        set_published_active(
            tenant_id=tenant_id,
            value=self.count_published_active(tenant_id),
        )
        set_retired_total(
            tenant_id=tenant_id,
            value=self.count_retired(tenant_id),
        )

    def _append_audit(
        self,
        draft: CaseDraftRow,
        *,
        from_state: str,
        to_state: str,
        actor_subject_id: str,
        content_hash: str,
        artifact_type: str = "case_blueprint",
    ) -> AuthoringAuditRow:
        identity = draft.blueprint_json.get("identity") or {}
        row = AuthoringAuditRow(
            id=str(uuid.uuid4()),
            artifact_type=artifact_type,
            draft_id=draft.id,
            case_id=str(identity.get("case_id") or ""),
            version=str(identity.get("version") or draft.blueprint_version),
            from_state=from_state,
            to_state=to_state,
            actor_subject_id=actor_subject_id,
            tenant_id=draft.tenant_id,
            at_utc=_utcnow(),
            content_hash=content_hash,
        )
        self._session.add(row)
        return row

    def _append_approval(
        self,
        draft: CaseDraftRow,
        *,
        kind: ApprovalKind,
        event_type: ApprovalEventType,
        actor: TransitionActor,
        dry_run_hash: str | None,
        submit_hash: str | None,
        content_hash: str,
        reason: str | None,
        supersedes_event_id: str | None = None,
        validation_context_hash: str | None = None,
    ) -> ApprovalEventRow:
        scope = (
            "authoring:approve_summative"
            if kind is ApprovalKind.SUMMATIVE
            else "authoring:approve"
        )
        row = ApprovalEventRow(
            id=str(uuid.uuid4()),
            draft_id=draft.id,
            event_type=event_type.value,
            kind=kind.value,
            actor_subject_id=actor.subject_id,
            actor_scope=scope,
            dry_run_result_hash=dry_run_hash,
            submit_dry_run_hash=submit_hash,
            content_hash=content_hash,
            validation_context_hash=validation_context_hash,
            reason=reason,
            supersedes_event_id=supersedes_event_id,
            occurred_at=_utcnow(),
        )
        self._session.add(row)
        return row

    def _submit_transition(self, draft_id: str) -> CaseStateTransitionRow | None:
        for row in reversed(self.list_transitions(draft_id)):
            if (
                row.from_state == CaseWorkflowState.DRAFT.value
                and row.to_state == CaseWorkflowState.IN_REVIEW.value
            ):
                return row
        return None

    def pinned_registry_snapshot(self, draft_id: str) -> RegistrySnapshot | None:
        """Registry freeze from DRAFT→IN_REVIEW submit, or None if not submitted."""
        return self._pinned_snapshot(draft_id)

    def _pinned_snapshot(self, draft_id: str) -> RegistrySnapshot | None:
        submit = self._submit_transition(draft_id)
        if submit is None or not submit.registry_snapshot_json:
            return None
        return RegistrySnapshot.from_json(submit.registry_snapshot_json)

    def _assert_content_hash_matches_submit(
        self, draft: CaseDraftRow, *, phase: str = "approve"
    ) -> tuple[str, str]:
        """IN_REVIEW freeze: approve/publish copies submit snapshot or refuses on drift."""
        current = _draft_content_hash(draft.blueprint_json)
        submit = self._submit_transition(draft.id)
        if submit is None or not submit.content_hash_snapshot:
            raise ContentHashDriftError("", current, phase=phase)
        if current != submit.content_hash_snapshot:
            raise ContentHashDriftError(
                submit.content_hash_snapshot,
                current,
                phase=phase,
            )
        return submit.content_hash_snapshot, submit.validation_context_hash or ""

    def _fresh_matching_dry_run(self, draft: CaseDraftRow) -> tuple[DryRunResult, str]:
        pinned = self._pinned_snapshot(draft.id)
        compiled = compile_blueprint(draft.blueprint_json, snapshot=pinned)
        if isinstance(compiled, CompilerErrors):
            raise BlueprintValidationError(tuple(e.message for e in compiled.errors))
        fixtures = self.list_fixtures(draft.id)
        fresh = execute_draft_to_in_review(draft, fixtures)
        submit_hash = submit_dry_run_hash(self.list_transitions(draft.id))
        assert_fresh_dry_run_matches_submit(submit_hash=submit_hash, fresh=fresh)
        return fresh, submit_hash or fresh.result_hash

    def transition(
        self,
        draft_id: str,
        *,
        to_state: CaseWorkflowState,
        actor: TransitionActor,
        reason: str | None = None,
        reason_code: str | None = None,
        reason_note: str | None = None,
    ) -> CaseStateTransitionRow:
        draft = self.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"unknown draft {draft_id}")

        from_state = CaseWorkflowState(draft.current_state)
        validate_transition_request(
            draft,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
        )

        if (from_state, to_state) == (
            CaseWorkflowState.APPROVED,
            CaseWorkflowState.PUBLISHED,
        ):
            return self.publish_draft(draft_id, actor=actor, reason=reason).transition

        if (from_state, to_state) == (
            CaseWorkflowState.PUBLISHED,
            CaseWorkflowState.RETIRED,
        ):
            return self.retire_published(
                draft_id,
                actor=actor,
                reason_code=reason_code,
                reason_note=reason_note,
            ).transition

        dry_run_hash: str | None = None
        registry_json = None
        context_hash: str | None = None
        content_snapshot: str | None = None

        if (from_state, to_state) == (
            CaseWorkflowState.DRAFT,
            CaseWorkflowState.IN_REVIEW,
        ):
            live = capture_registry_snapshot(draft.blueprint_json)
            compiled = compile_blueprint(draft.blueprint_json, snapshot=live)
            if isinstance(compiled, CompilerErrors):
                raise BlueprintValidationError(tuple(e.message for e in compiled.errors))
            fixtures = self.list_fixtures(draft_id)
            dry_run: DryRunResult = execute_draft_to_in_review(draft, fixtures)
            dry_run_hash = dry_run.result_hash
            registry_json = live.to_json()
            context_hash = live.validation_context_hash()
            content_snapshot = _draft_content_hash(draft.blueprint_json)

        if (from_state, to_state) == (
            CaseWorkflowState.IN_REVIEW,
            CaseWorkflowState.APPROVED,
        ):
            content_snapshot, context_hash = self._assert_content_hash_matches_submit(draft)
            fresh, submit_hash = self._fresh_matching_dry_run(draft)
            dry_run_hash = fresh.result_hash
            self._append_approval(
                draft,
                kind=ApprovalKind.FORMATIVE,
                event_type=ApprovalEventType.GRANT,
                actor=actor,
                dry_run_hash=dry_run_hash,
                submit_hash=submit_hash,
                content_hash=content_snapshot,
                reason=reason,
                validation_context_hash=context_hash,
            )

        if content_snapshot is None:
            content_snapshot = _draft_content_hash(draft.blueprint_json)
        now = _utcnow()
        required_scope = _required_scope_for(
            from_state,
            to_state,
            assessment_mode=assessment_mode_from_blueprint(draft.blueprint_json)
            if to_state in (CaseWorkflowState.PUBLISHED, CaseWorkflowState.RETIRED)
            else None,
        ) or ""
        transition = CaseStateTransitionRow(
            id=str(uuid.uuid4()),
            draft_id=draft_id,
            from_state=from_state.value,
            to_state=to_state.value,
            actor_subject_id=actor.subject_id,
            actor_scope=required_scope,
            reason=reason,
            dry_run_result_hash=dry_run_hash,
            content_hash_snapshot=content_snapshot,
            validation_context_hash=context_hash,
            registry_snapshot_json=registry_json,
            occurred_at=now,
        )
        draft.current_state = to_state.value
        draft.updated_at = now
        self._session.add(transition)
        self._append_audit(
            draft,
            from_state=from_state.value,
            to_state=to_state.value,
            actor_subject_id=actor.subject_id,
            content_hash=content_snapshot,
        )
        self._session.flush()
        return transition

    def publish_draft(
        self,
        draft_id: str,
        *,
        actor: TransitionActor,
        reason: str | None = None,
    ) -> PublishResult:
        draft = self.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"unknown draft {draft_id}")

        events = self.list_approval_events(draft_id)
        fixtures = self.list_fixtures(draft_id)
        pinned = self._pinned_snapshot(draft_id)
        case_id, version = case_identity_from_draft(draft)
        prior = self.list_published_versions_for_case(case_id)

        backfill_clinical_reviewer_if_needed(draft, events)
        validate_publish_preconditions(
            draft,
            actor,
            events,
            fixtures=fixtures,
            pinned_snapshot=pinned,
            submit_dry_run_hash_value=submit_dry_run_hash(self.list_transitions(draft_id)),
            prior_publishes=prior,
        )

        content_snapshot, context_hash = self._assert_content_hash_matches_submit(
            draft,
            phase="publish",
        )
        submit = self._submit_transition(draft.id)
        registry_json = submit.registry_snapshot_json if submit else None
        mode = assessment_mode_from_blueprint(draft.blueprint_json)
        provenance = dict(draft.blueprint_json.get("provenance") or {})
        clinical_reviewer = provenance.get("clinical_reviewer")
        dry_run_hash = submit_dry_run_hash(self.list_transitions(draft_id))
        now = _utcnow()

        published = PublishedCaseVersionRow(
            id=str(uuid.uuid4()),
            draft_id=draft.id,
            tenant_id=draft.tenant_id,
            case_id=case_id,
            version=version,
            assessment_mode=mode.value,
            content_hash=content_snapshot,
            harness_version=HARNESS_VERSION,
            published_at=now,
            published_by=actor.subject_id,
            registry_snapshot_json=registry_json,
            validation_context_hash=context_hash,
            fixture_hashes=fixture_hashes(fixtures),
            clinical_reviewer=str(clinical_reviewer) if clinical_reviewer else None,
        )
        transition = CaseStateTransitionRow(
            id=str(uuid.uuid4()),
            draft_id=draft_id,
            from_state=CaseWorkflowState.APPROVED.value,
            to_state=CaseWorkflowState.PUBLISHED.value,
            actor_subject_id=actor.subject_id,
            actor_scope=_required_scope_for(
                CaseWorkflowState.APPROVED,
                CaseWorkflowState.PUBLISHED,
                assessment_mode=mode,
            )
            or "",
            reason=reason,
            dry_run_result_hash=dry_run_hash,
            content_hash_snapshot=content_snapshot,
            validation_context_hash=context_hash,
            registry_snapshot_json=registry_json,
            occurred_at=now,
        )
        draft.current_state = CaseWorkflowState.PUBLISHED.value
        draft.updated_at = now
        self._session.add(published)
        self._session.add(transition)
        self._append_audit(
            draft,
            from_state=CaseWorkflowState.APPROVED.value,
            to_state=CaseWorkflowState.PUBLISHED.value,
            actor_subject_id=actor.subject_id,
            content_hash=content_snapshot,
            artifact_type="published_case_version",
        )
        self._session.flush()
        identity = draft.blueprint_json.get("identity") or {}
        tier = str(identity.get("corpus_tier") or "unknown")
        record_publish(tenant_id=draft.tenant_id, tier=tier)
        self._refresh_publish_gauges(draft.tenant_id)
        return PublishResult(published=published, transition=transition)

    def retire_published(
        self,
        draft_id: str,
        *,
        actor: TransitionActor,
        reason_code: str | None,
        reason_note: str | None = None,
    ) -> RetireResult:
        """
        Single-txn retirement: state → RETIRED, tombstone patch, audit insert.

        Partial retirement is forbidden — all three writes flush together.
        """
        draft = self.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"unknown draft {draft_id}")

        published = self.get_published_for_draft(draft_id)
        validated_code = validate_retire_preconditions(
            draft,
            actor,
            published=published,
            reason_code=reason_code,
            reason_note=reason_note,
        )
        assert published is not None  # validated above

        mode = assessment_mode_from_blueprint(draft.blueprint_json)
        content_snapshot = published.content_hash
        now = _utcnow()
        note = reason_note if reason_note else None

        published.retired_at = now
        published.retired_by = actor.subject_id
        published.retired_reason_code = validated_code.value
        published.retired_reason_text = note

        retired = RetiredCaseVersionRow(
            id=str(uuid.uuid4()),
            published_case_version_id=published.id,
            draft_id=draft.id,
            tenant_id=draft.tenant_id,
            case_id=published.case_id,
            version=published.version,
            retired_at=now,
            retired_by=actor.subject_id,
            reason_code=validated_code.value,
            reason_text=note,
        )
        transition = CaseStateTransitionRow(
            id=str(uuid.uuid4()),
            draft_id=draft_id,
            from_state=CaseWorkflowState.PUBLISHED.value,
            to_state=CaseWorkflowState.RETIRED.value,
            actor_subject_id=actor.subject_id,
            actor_scope=_required_scope_for(
                CaseWorkflowState.PUBLISHED,
                CaseWorkflowState.RETIRED,
                assessment_mode=mode,
            )
            or "",
            reason=note,
            dry_run_result_hash=None,
            content_hash_snapshot=content_snapshot,
            validation_context_hash=published.validation_context_hash,
            registry_snapshot_json=published.registry_snapshot_json,
            occurred_at=now,
        )
        draft.current_state = CaseWorkflowState.RETIRED.value
        draft.updated_at = now
        self._session.add(retired)
        self._session.add(transition)
        self._append_audit(
            draft,
            from_state=CaseWorkflowState.PUBLISHED.value,
            to_state=CaseWorkflowState.RETIRED.value,
            actor_subject_id=actor.subject_id,
            content_hash=content_snapshot,
            artifact_type="retired_case_version",
        )
        self._session.flush()
        record_retire(tenant_id=draft.tenant_id, reason_code=validated_code.value)
        self._refresh_publish_gauges(draft.tenant_id)
        return RetireResult(published=published, retired=retired, transition=transition)

    def record_summative_approval(
        self,
        draft_id: str,
        *,
        actor: TransitionActor,
        reason: str | None = None,
    ) -> SummativeApprovalResult:
        """
        Append a summative GRANT. Does not publish (Phase D).

        Distinct-principal and hash-match checks live here so a crafted
        API payload cannot skip them. Third+ distinct vouchers are recorded
        with ``SummativeGrantOutcome.GRANT_RECORDED_NO_STATE_CHANGE``.
        """
        draft = self.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"unknown draft {draft_id}")
        if draft.current_state != CaseWorkflowState.IN_REVIEW.value:
            raise InvalidTransitionError(
                f"summative approval requires IN_REVIEW, draft is {draft.current_state}"
            )
        events = self.list_approval_events(draft_id)
        was_ready = summative_ready(events)
        validate_summative_approver(draft, actor, events)
        content_snapshot, context_hash = self._assert_content_hash_matches_submit(draft)
        fresh, submit_hash = self._fresh_matching_dry_run(draft)
        row = self._append_approval(
            draft,
            kind=ApprovalKind.SUMMATIVE,
            event_type=ApprovalEventType.GRANT,
            actor=actor,
            dry_run_hash=fresh.result_hash,
            submit_hash=submit_hash,
            content_hash=content_snapshot,
            reason=reason,
            validation_context_hash=context_hash,
        )
        self._append_audit(
            draft,
            from_state=CaseWorkflowState.IN_REVIEW.value,
            to_state=CaseWorkflowState.IN_REVIEW.value,
            actor_subject_id=actor.subject_id,
            content_hash=content_snapshot,
            artifact_type="summative_approval",
        )
        self._session.flush()
        all_events = events + [row]
        now_ready = summative_ready(all_events)
        outcome = (
            SummativeGrantOutcome.GRANT_RECORDED_NO_STATE_CHANGE
            if was_ready
            else SummativeGrantOutcome.GRANT_RECORDED
        )
        return SummativeApprovalResult(
            event=row,
            outcome=outcome,
            summative_ready=now_ready,
        )

    def withdraw_approval(
        self,
        draft_id: str,
        *,
        grant_id: str,
        actor: TransitionActor,
        reason: str | None = None,
    ) -> ApprovalEventRow:
        """Append a WITHDRAW. Never DELETE or mutate the original GRANT."""
        draft = self.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"unknown draft {draft_id}")
        grant = self._session.get(ApprovalEventRow, grant_id)
        if grant is None or grant.draft_id != draft_id:
            raise KeyError(f"unknown approval grant {grant_id}")
        if grant.event_type != ApprovalEventType.GRANT.value:
            raise InvalidTransitionError("can only withdraw a GRANT")
        if grant.kind != ApprovalKind.SUMMATIVE.value:
            raise InvalidTransitionError(
                "formative approval is a state transition; revert instead of withdraw"
            )
        if draft.current_state != CaseWorkflowState.IN_REVIEW.value:
            raise InvalidTransitionError(
                "summative withdraw is only allowed while IN_REVIEW"
            )
        is_self = actor.subject_id == grant.actor_subject_id
        if not is_self and SCOPE_ADMIN not in actor.scopes:
            raise ScopeDeniedError("withdraw requires the original principal or authoring:admin")
        events = self.list_approval_events(draft_id)
        already = any(
            e.event_type == ApprovalEventType.WITHDRAW.value
            and e.supersedes_event_id == grant_id
            for e in events
        )
        if already:
            raise InvalidTransitionError("grant already withdrawn")
        snapshot = _draft_content_hash(draft.blueprint_json)
        row = self._append_approval(
            draft,
            kind=ApprovalKind.SUMMATIVE,
            event_type=ApprovalEventType.WITHDRAW,
            actor=actor,
            dry_run_hash=None,
            submit_hash=submit_dry_run_hash(self.list_transitions(draft_id)),
            content_hash=snapshot,
            reason=reason,
            supersedes_event_id=grant_id,
            validation_context_hash=grant.validation_context_hash,
        )
        self._append_audit(
            draft,
            from_state=draft.current_state,
            to_state=draft.current_state,
            actor_subject_id=actor.subject_id,
            content_hash=snapshot,
            artifact_type="summative_withdraw",
        )
        self._session.flush()
        return row
